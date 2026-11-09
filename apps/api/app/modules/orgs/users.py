"""Accounts a tenant (organization) creates: `<tenant code>.<name>` usernames with a set or random
password, members of the tenant from the start. They skip identity verification (the tenant
vouches for them), cannot leave, and only the tenant's admins reset, disable or delete them: an
admin acts on member accounts, the owner on any account the tenant created. Passwords are shown
once, in the response that sets them; every reset forces the account to pick its own at its next
sign-in."""

import asyncio
import secrets
import string

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import conflict, forbidden
from app.core.logging import get_logger
from app.core.money import money_label
from app.core.security import hash_password
from app.modules.account import service as account_service
from app.modules.billing import service as billing_service
from app.modules.orchestrator import queries as orchestrator_queries
from app.modules.orgs import service
from app.modules.orgs.access import require_org_role
from app.modules.orgs.models import ROLE_MEMBER, ROLE_OWNER, Organization, OrgMember
from app.modules.orgs.schemas import (
    ManagedPasswordOut,
    ManagedPasswordReset,
    ManagedUserCreatedOut,
    ManagedUsersCreate,
    ManagedUserUpdate,
    MemberOut,
)

logger = get_logger(__name__)

_PASSWORD_ALPHABET = "".join(c for c in string.ascii_letters + string.digits if c not in "0Oo1lI")
RANDOM_PASSWORD_LENGTH = 16
_HASH_CONCURRENCY = 8


def random_password() -> str:
    """16 characters without look-alikes (no 0/O/o, 1/l/I)."""
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(RANDOM_PASSWORD_LENGTH))


async def _hashes(passwords: list[str]) -> list[str]:
    """bcrypt for every distinct password, a few at a time (a shared password hashes once)."""
    gate = asyncio.Semaphore(_HASH_CONCURRENCY)
    distinct = list(dict.fromkeys(passwords))

    async def one(plain: str) -> str:
        async with gate:
            return await hash_password(plain)

    hashed = dict(zip(distinct, await asyncio.gather(*(one(p) for p in distinct)), strict=True))
    return [hashed[p] for p in passwords]


async def create_users(
    session: AsyncSession, user_id: int, body: ManagedUsersCreate
) -> list[ManagedUserCreatedOut]:
    org, me = await require_org_role(session, user_id, service.ADMINS, lock=True)
    names = body.names
    if len(set(names)) != len(names):
        raise conflict(key="orgs.usernamesDuplicate")
    usernames = [f"{org.code}.{n}" for n in names]
    taken = await account_service.username_taken(session, usernames)
    if taken:
        raise conflict(
            key="orgs.usernamesTaken", params={"names": ", ".join(taken[:5])}, detail=taken
        )
    limit = get_settings().org_max_members
    current = await service.member_count(session, org.id)
    if current + len(names) > limit:
        raise conflict(key="orgs.memberQuota", params={"max": limit})
    passwords = [body.password or random_password() for _ in names]
    out: list[ManagedUserCreatedOut] = []
    try:
        for username, password, password_hash in zip(
            usernames, passwords, await _hashes(passwords), strict=True
        ):
            user = await account_service.create_managed_user(
                session, username=username, password_hash=password_hash, org_id=org.id
            )
            member = OrgMember(
                org_id=org.id,
                user_id=user.id,
                role=ROLE_MEMBER,
                remark=body.remark,
                invited_by=me.user_id,
                consent_version=service.CONSENT_VERSION,
            )
            session.add(member)
            await session.flush()
            out.append(
                ManagedUserCreatedOut(
                    member_id=member.id, user_id=user.id, username=username, password=password
                )
            )
    except IntegrityError:
        await session.rollback()
        raise conflict(key="orgs.usernamesTaken", params={"names": ""}) from None
    await session.commit()
    logger.info("org_users_created", org_uuid=org.uuid, count=len(out))
    return out


async def _managed_target(
    session: AsyncSession, user_id: int, member_id: int
) -> tuple[Organization, OrgMember]:
    """The tenant member behind member_id when it is an account this tenant created and the
    caller may act on it (the owner on any, an admin on members)."""
    org, me = await require_org_role(session, user_id, service.ADMINS, lock=True)
    target = await service.member_in_org(session, org.id, member_id)
    if not await service.is_managed_by(session, target):
        raise conflict(key="orgs.notManaged")
    if target.id == me.id:
        raise conflict(key="orgs.managedSelf")
    if target.role != ROLE_MEMBER and me.role != ROLE_OWNER:
        raise forbidden(key="orgs.roleNotAllowed")
    return org, target


async def reset_password(
    session: AsyncSession, user_id: int, member_id: int, body: ManagedPasswordReset
) -> ManagedPasswordOut:
    _org, target = await _managed_target(session, user_id, member_id)
    user = await account_service.lock_user(session, target.user_id)
    password = body.password or random_password()
    await account_service.set_managed_password(user, password)
    await session.commit()
    logger.info("org_user_password_reset", member_id=member_id)
    return ManagedPasswordOut(username=user.username or "", password=password)


async def set_login(
    session: AsyncSession, user_id: int, member_id: int, body: ManagedUserUpdate
) -> MemberOut:
    _org, target = await _managed_target(session, user_id, member_id)
    user = await account_service.lock_user(session, target.user_id)
    account_service.set_login_disabled(user, body.login_disabled)
    await session.commit()
    return (await service.member_outs(session, [target]))[0]


async def delete_user(session: AsyncSession, user_id: int, member_id: int) -> None:
    """Only an empty account goes: no unreleased instance, no data disk, zero balance (take the
    balance back first). The membership ends (gateways and storage mounts follow) and the account
    is marked deleted in the same transaction."""
    org, target = await _managed_target(session, user_id, member_id)
    leftovers = await orchestrator_queries.deletion_leftovers(session, target.user_id)
    if leftovers["instances"] or leftovers["disks"]:
        raise conflict(
            key="orgs.managedHasResources",
            params={
                "instances": len(leftovers["instances"]),
                "disks": len(leftovers["disks"]),
            },
            detail=leftovers,
        )
    balance = await billing_service.get_balance(session, target.user_id)
    if balance != 0:
        raise conflict(key="orgs.managedHasBalance", params={"balance": money_label(balance)})
    user = await account_service.lock_user(session, target.user_id)
    await service.remove_members(session, org, [target])
    account_service.scrub_deleted_account(user)
    await session.commit()
    logger.info("org_user_deleted", org_uuid=org.uuid, user_id=target.user_id)
