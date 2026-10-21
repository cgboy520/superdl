"""Organizations: membership, invitations, the read-only finance view of members and the admin
operations. Lock order: organization row → member / invitation rows. Removing members or
dissolving runs the registered listeners in the same transaction (netgw drops the members'
attachments); listeners never commit, and an exception rolls the whole change back."""

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.constants import ADMIN_LIST_CAP
from app.core.errors import AppError, ErrorCode, conflict, forbidden, not_found
from app.core.handles import mask_handle
from app.core.idempotency import (
    IDEMPOTENCY_WINDOW,
    find_replay,
    insert_idempotent,
    request_fingerprint,
)
from app.core.logging import get_logger
from app.core.money import as_price, platform_currency
from app.core.outbox import OutboxTask, enqueue, outbox_handler
from app.core.pagination import RawPage, paginate_by_id
from app.core.servercopy import copy as server_copy
from app.core.sqlutil import like_escape
from app.core.timeutil import billing_period_range, current_billing_period, ensure_utc, now_utc
from app.modules.account import service as account_service
from app.modules.billing import service as billing_service
from app.modules.notify import service as notify_service
from app.modules.orchestrator import queries as orchestrator_queries
from app.modules.orgs.models import (
    INVITE_ACCEPTED,
    INVITE_DECLINED,
    INVITE_EXPIRED,
    INVITE_PENDING,
    INVITE_REVOKED,
    ORG_ACTIVE,
    ORG_DISSOLVED,
    ROLE_ADMIN,
    ROLE_MEMBER,
    ROLE_OWNER,
    Organization,
    OrgInvitation,
    OrgMember,
)
from app.modules.orgs.schemas import (
    AdminOrgDetailOut,
    AdminOrgInvitationOut,
    AdminOrgMemberOut,
    AdminOrgOut,
    DisclosureOut,
    FinanceTotalsOut,
    InvitationCreate,
    InvitationOut,
    MemberFinanceOut,
    MemberOut,
    MemberUpdate,
    MyOrgOut,
    OrgContactOut,
    OrgCreate,
    OrgFinanceOut,
    OrgOut,
    OrgRefOut,
    ReceivedInvitationOut,
)

if TYPE_CHECKING:
    from app.modules.account.models import User

logger = get_logger(__name__)

CONSENT_VERSION = "1"
DISCLOSURE_ITEMS = (
    "identity",
    "available_balance",
    "month_to_date",
    "burn_rate",
    "instances",
    "routed_access",
)
INVITATION_TTL = timedelta(days=7)
ADMINS = (ROLE_OWNER, ROLE_ADMIN)
ALL_ROLES = (ROLE_OWNER, ROLE_ADMIN, ROLE_MEMBER)
INVITE_NOTIFY_TASK = "orgs.invite_notify"
_ROLE_ORDER = {ROLE_OWNER: 0, ROLE_ADMIN: 1, ROLE_MEMBER: 2}

MembersRemovedListener = Callable[[AsyncSession, int, set[int]], Awaitable[None]]
OrgDissolvedListener = Callable[[AsyncSession, int], Awaitable[None]]
_members_removed_listeners: list[MembersRemovedListener] = []
_org_dissolved_listeners: list[OrgDissolvedListener] = []


def register_members_removed_listener(listener: MembersRemovedListener) -> None:
    """listener(session, org_id, user_ids) runs after the membership rows are gone, before
    commit."""
    if listener not in _members_removed_listeners:
        _members_removed_listeners.append(listener)


def register_org_dissolved_listener(listener: OrgDissolvedListener) -> None:
    """listener(session, org_id) runs after the organization is marked dissolved, before
    commit."""
    if listener not in _org_dissolved_listeners:
        _org_dissolved_listeners.append(listener)


def disclosure() -> DisclosureOut:
    return DisclosureOut(version=CONSENT_VERSION, items=list(DISCLOSURE_ITEMS))


@dataclass(frozen=True)
class MemberLabel:
    member_id: int
    label: str


def _label(remark: str | None, email: str | None) -> str:
    return remark or mask_handle(email)


def _org_out(org: Organization) -> OrgOut:
    return OrgOut(uuid=org.uuid, name=org.name, status=org.status, created_at=org.created_at)


def _invitation_status(inv: OrgInvitation) -> str:
    if inv.status == INVITE_PENDING and ensure_utc(inv.expires_at) <= now_utc():
        return INVITE_EXPIRED
    return inv.status


# ---------------------------------------------------------------- public surface for other modules


async def membership_of(session: AsyncSession, user_id: int) -> OrgMember | None:
    """The user's membership; only active organizations have members."""
    return await session.scalar(select(OrgMember).where(OrgMember.user_id == user_id))


async def require_org_role(
    session: AsyncSession, user_id: int, roles: Iterable[str], *, lock: bool = False
) -> tuple[Organization, OrgMember]:
    """The caller's organization and membership; not a member → 404, other role → 403. lock=True
    takes the organization row lock first and re-reads the membership under it."""
    member = await membership_of(session, user_id)
    if member is None:
        raise not_found(key="orgs.notMember")
    if lock:
        org = await _lock_org(session, member.org_id)
        member = await session.scalar(
            select(OrgMember)
            .where(OrgMember.user_id == user_id, OrgMember.org_id == org.id)
            .execution_options(populate_existing=True)
        )
        if member is None:
            raise not_found(key="orgs.notMember")
    else:
        org = await session.get(Organization, member.org_id)
        if org is None:
            raise not_found(key="orgs.notMember")
    if member.role not in tuple(roles):
        raise forbidden(key="orgs.roleNotAllowed")
    return org, member


async def lock_org(session: AsyncSession, org_id: int) -> Organization:
    """FOR UPDATE on the organization row (the first lock of every organization-scoped write)."""
    return await _lock_org(session, org_id)


async def active_member_ids(session: AsyncSession, org_ids: Iterable[int]) -> dict[int, set[int]]:
    """org_id → member user ids (every requested organization is present)."""
    ids = list(set(org_ids))
    out: dict[int, set[int]] = {i: set() for i in ids}
    if not ids:
        return out
    rows = await session.execute(
        select(OrgMember.org_id, OrgMember.user_id).where(OrgMember.org_id.in_(ids))
    )
    for org_id, user_id in rows.tuples():
        out[org_id].add(user_id)
    return out


async def org_statuses(session: AsyncSession, org_ids: Iterable[int]) -> dict[int, str]:
    """org_id → status; unknown ids are absent."""
    ids = list(set(org_ids))
    if not ids:
        return {}
    rows = await session.execute(
        select(Organization.id, Organization.status).where(Organization.id.in_(ids))
    )
    return dict(rows.tuples().all())


async def member_labels(
    session: AsyncSession, org_id: int, user_ids: Iterable[int]
) -> dict[int, MemberLabel]:
    """user_id → (member_id, remark or masked email) for members of org_id; others are absent."""
    ids = list(set(user_ids))
    if not ids:
        return {}
    members = (
        await session.scalars(
            select(OrgMember).where(OrgMember.org_id == org_id, OrgMember.user_id.in_(ids))
        )
    ).all()
    emails = await account_service.emails_by_user(session, [m.user_id for m in members])
    return {m.user_id: MemberLabel(m.id, _label(m.remark, emails.get(m.user_id))) for m in members}


async def org_id_by_uuid(session: AsyncSession, uuid: str) -> int | None:
    return await session.scalar(select(Organization.id).where(Organization.uuid == uuid))


async def org_refs(session: AsyncSession, org_ids: Iterable[int]) -> dict[int, tuple[str, str]]:
    """org_id → (uuid, name); unknown ids are absent."""
    ids = list(set(org_ids))
    if not ids:
        return {}
    rows = await session.execute(
        select(Organization.id, Organization.uuid, Organization.name).where(
            Organization.id.in_(ids)
        )
    )
    return {oid: (uuid, name) for oid, uuid, name in rows.tuples()}


async def orgs_of_users(session: AsyncSession, user_ids: Iterable[int]) -> dict[int, OrgRefOut]:
    """user_id → organization reference (admin tenant rows); users outside one are absent."""
    ids = list(set(user_ids))
    if not ids:
        return {}
    rows = await session.execute(
        select(OrgMember.user_id, OrgMember.role, Organization.uuid, Organization.name)
        .join(Organization, Organization.id == OrgMember.org_id)
        .where(OrgMember.user_id.in_(ids))
    )
    return {
        uid: OrgRefOut.model_validate({"uuid": uuid, "name": name, "role": role})
        for uid, role, uuid, name in rows.tuples()
    }


async def owned_org_other_members(session: AsyncSession, user_id: int) -> int:
    """How many other members the organization owned by user_id has (0 when not an owner)."""
    member = await membership_of(session, user_id)
    if member is None or member.role != ROLE_OWNER:
        return 0
    return await _member_count(session, member.org_id) - 1


async def drop_deleted_user(session: AsyncSession, user_id: int) -> None:
    """Account deletion (no commit): a member leaves, a sole owner's organization dissolves. The
    caller has already refused an owner with other members."""
    member = await membership_of(session, user_id)
    if member is None:
        return
    org = await _lock_org(session, member.org_id)
    if member.role == ROLE_OWNER:
        await _dissolve(session, org)
        logger.info("org_dissolved_by_deletion", org_uuid=org.uuid, user_id=user_id)
        return
    await _remove(session, org, [member])


# ---------------------------------------------------------------- internals


async def _lock_org(session: AsyncSession, org_id: int) -> Organization:
    org = await session.get(Organization, org_id, with_for_update=True, populate_existing=True)
    if org is None:
        raise not_found(key="orgs.notFound")
    return org


async def _member_count(session: AsyncSession, org_id: int) -> int:
    return (
        await session.scalar(
            select(func.count()).select_from(OrgMember).where(OrgMember.org_id == org_id)
        )
    ) or 0


async def _members(session: AsyncSession, org_id: int) -> list[OrgMember]:
    rows = (await session.scalars(select(OrgMember).where(OrgMember.org_id == org_id))).all()
    return sorted(rows, key=lambda m: (_ROLE_ORDER[m.role], ensure_utc(m.joined_at), m.id))


async def _member_in_org(session: AsyncSession, org_id: int, member_id: int) -> OrgMember:
    member = await session.scalar(
        select(OrgMember).where(OrgMember.id == member_id, OrgMember.org_id == org_id)
    )
    if member is None:
        raise not_found(key="orgs.memberNotFound")
    return member


async def _remove(session: AsyncSession, org: Organization, members: list[OrgMember]) -> None:
    ids = {m.user_id for m in members}
    for m in members:
        await session.delete(m)
    await session.flush()
    for listener in _members_removed_listeners:
        await listener(session, org.id, ids)
    logger.info("org_members_removed", org_uuid=org.uuid, count=len(ids))


async def _dissolve(session: AsyncSession, org: Organization) -> list[OrgMember]:
    """Revoke pending invitations, drop every membership, mark dissolved, run the dissolved
    listeners; no commit."""
    now = now_utc()
    members = await _members(session, org.id)
    await session.execute(
        update(OrgInvitation)
        .where(OrgInvitation.org_id == org.id, OrgInvitation.status == INVITE_PENDING)
        .values(status=INVITE_REVOKED, responded_at=now)
    )
    for m in members:
        await session.delete(m)
    org.status = ORG_DISSOLVED
    org.dissolved_at = now
    await session.flush()
    for listener in _org_dissolved_listeners:
        await listener(session, org.id)
    return members


async def _notify(session: AsyncSession, user_ids: Iterable[int], key: str, org_name: str) -> None:
    for uid in user_ids:
        await notify_service.notify(
            session,
            uid,
            type_="org",
            title=server_copy(f"orgs.{key}.title", org=org_name),
            content=server_copy(f"orgs.{key}.content", org=org_name),
        )


async def _member_outs(session: AsyncSession, members: list[OrgMember]) -> list[MemberOut]:
    ids = [m.user_id for m in members]
    emails = await account_service.emails_by_user(session, ids)
    statuses = await account_service.user_statuses(session, ids)
    return [
        MemberOut.model_validate(
            {
                "member_id": m.id,
                "role": m.role,
                "label": _label(m.remark, emails.get(m.user_id)),
                "email_masked": mask_handle(emails.get(m.user_id)),
                "remark": m.remark,
                "status": statuses.get(m.user_id, "deleted"),
                "joined_at": m.joined_at,
            }
        )
        for m in members
    ]


# ---------------------------------------------------------------- the caller's organization


async def create_org(
    session: AsyncSession, user_id: int, body: OrgCreate, idempotency_key: str | None
) -> tuple[Organization, bool]:
    """Create an organization owned by the caller; (organization, created)."""
    fingerprint = request_fingerprint(user_id, body.name)
    if idempotency_key:
        existing = await find_replay(
            session,
            Organization,
            owner_col=Organization.created_by,
            owner_id=user_id,
            key=idempotency_key,
            window=IDEMPOTENCY_WINDOW,
            fingerprint=fingerprint,
        )
        if existing is not None:
            return existing, False
    if await membership_of(session, user_id) is not None:
        raise conflict(key="orgs.alreadyMember")
    org = Organization(
        uuid=uuid4().hex,
        name=body.name,
        created_by=user_id,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
    )
    result = await insert_idempotent(
        session,
        org,
        model=Organization,
        owner_col=Organization.created_by,
        owner_id=user_id,
        key=idempotency_key,
        window=IDEMPOTENCY_WINDOW,
        fingerprint=fingerprint,
    )
    if result is not org:
        return result, False
    session.add(
        OrgMember(org_id=org.id, user_id=user_id, role=ROLE_OWNER, consent_version=CONSENT_VERSION)
    )
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise conflict(key="orgs.alreadyMember") from None
    await session.commit()
    await session.refresh(org)
    logger.info("org_created", org_uuid=org.uuid, user_id=user_id)
    return org, True


async def _received_rows(
    session: AsyncSession, user: "User"
) -> list[tuple[OrgInvitation, Organization]]:
    if not user.email or user.email_verified_at is None:
        return []
    rows = await session.execute(
        select(OrgInvitation, Organization)
        .join(Organization, Organization.id == OrgInvitation.org_id)
        .where(
            OrgInvitation.email == user.email,
            OrgInvitation.status == INVITE_PENDING,
            OrgInvitation.expires_at > now_utc(),
            Organization.status == ORG_ACTIVE,
        )
        .order_by(OrgInvitation.id.desc())
    )
    return [(inv, org) for inv, org in rows.tuples()]


async def my_org(session: AsyncSession, user: "User") -> MyOrgOut:
    received = len(await _received_rows(session, user))
    member = await membership_of(session, user.id)
    if member is None:
        return MyOrgOut(
            org=None,
            role=None,
            member_id=None,
            member_count=None,
            admins=[],
            received_invitations=received,
        )
    org = await session.get(Organization, member.org_id)
    if org is None:
        raise not_found(key="orgs.notMember")
    members = await _members(session, org.id)
    admins = [m for m in members if m.role in ADMINS]
    emails = await account_service.emails_by_user(session, [m.user_id for m in admins])
    return MyOrgOut.model_validate(
        {
            "org": _org_out(org),
            "role": member.role,
            "member_id": member.id,
            "member_count": len(members),
            "admins": [
                OrgContactOut.model_validate(
                    {"role": m.role, "label": mask_handle(emails.get(m.user_id))}
                )
                for m in admins
            ],
            "received_invitations": received,
        }
    )


async def rename_org(session: AsyncSession, user_id: int, name: str) -> Organization:
    org, _ = await require_org_role(session, user_id, (ROLE_OWNER,), lock=True)
    org.name = name
    await session.commit()
    await session.refresh(org)
    return org


async def transfer_owner(session: AsyncSession, user_id: int, member_id: int) -> Organization:
    """The owner becomes an admin and the target the owner (demote, flush, promote: the one-owner
    index is not deferrable)."""
    org, me = await require_org_role(session, user_id, (ROLE_OWNER,), lock=True)
    target = await _member_in_org(session, org.id, member_id)
    if target.id == me.id:
        raise conflict(key="orgs.transferTargetInvalid")
    await _swap_owner(session, me, target)
    await _notify(session, [target.user_id], "owner_transferred", org.name)
    await session.commit()
    logger.info("org_owner_transferred", org_uuid=org.uuid, member_id=target.id)
    return org


async def _swap_owner(session: AsyncSession, old: OrgMember, new: OrgMember) -> None:
    old.role = ROLE_ADMIN
    await session.flush()
    new.role = ROLE_OWNER
    await session.flush()


async def dissolve_org(session: AsyncSession, user_id: int, confirm_name: str) -> Organization:
    org, me = await require_org_role(session, user_id, (ROLE_OWNER,), lock=True)
    if " ".join(confirm_name.split()) != org.name:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="orgs.confirmNameMismatch")
    members = await _dissolve(session, org)
    await _notify(session, [m.user_id for m in members if m.id != me.id], "dissolved", org.name)
    await session.commit()
    await session.refresh(org)
    logger.info("org_dissolved", org_uuid=org.uuid, user_id=user_id)
    return org


async def leave_org(session: AsyncSession, user_id: int) -> None:
    org, me = await require_org_role(session, user_id, ALL_ROLES, lock=True)
    if me.role == ROLE_OWNER:
        raise conflict(key="orgs.ownerCannotLeave")
    await _remove(session, org, [me])
    await session.commit()


async def list_members(session: AsyncSession, user_id: int) -> list[MemberOut]:
    org, _ = await require_org_role(session, user_id, ADMINS)
    return await _member_outs(session, await _members(session, org.id))


async def update_member(
    session: AsyncSession, user_id: int, member_id: int, body: MemberUpdate
) -> MemberOut:
    """Owner: role and remark; admin: remark only. The owner role moves only by transfer."""
    org, me = await require_org_role(session, user_id, ADMINS, lock=True)
    target = await _member_in_org(session, org.id, member_id)
    fields = body.model_fields_set
    if "role" in fields and body.role is not None and body.role != target.role:
        if me.role != ROLE_OWNER:
            raise forbidden(key="orgs.roleNotAllowed")
        if target.role == ROLE_OWNER:
            raise conflict(key="orgs.ownerRoleViaTransfer")
        target.role = body.role
    if "remark" in fields:
        target.remark = body.remark or None
    await session.commit()
    return (await _member_outs(session, [target]))[0]


async def remove_member(session: AsyncSession, user_id: int, member_id: int) -> None:
    """Owner removes anyone else, an admin removes members only; the member is notified."""
    org, me = await require_org_role(session, user_id, ADMINS, lock=True)
    target = await _member_in_org(session, org.id, member_id)
    if target.id == me.id:
        raise conflict(key="orgs.useLeave")
    if target.role == ROLE_OWNER:
        raise conflict(key="orgs.cannotRemoveOwner")
    if target.role == ROLE_ADMIN and me.role != ROLE_OWNER:
        raise forbidden(key="orgs.roleNotAllowed")
    await _remove(session, org, [target])
    await _notify(session, [target.user_id], "removed", org.name)
    await session.commit()


# ---------------------------------------------------------------- invitations


async def _invitation_outs(session: AsyncSession, rows: list[OrgInvitation]) -> list[InvitationOut]:
    emails = await account_service.emails_by_user(session, {r.invited_by for r in rows})
    return [
        InvitationOut.model_validate(
            {
                "uuid": r.uuid,
                "email": r.email,
                "role": r.role,
                "remark": r.remark,
                "status": _invitation_status(r),
                "invited_by": mask_handle(emails.get(r.invited_by)),
                "created_at": r.created_at,
                "expires_at": r.expires_at,
            }
        )
        for r in rows
    ]


async def list_invitations(session: AsyncSession, user_id: int) -> list[InvitationOut]:
    """Sent invitations, newest first (capped)."""
    org, _ = await require_org_role(session, user_id, ADMINS)
    rows = list(
        (
            await session.scalars(
                select(OrgInvitation)
                .where(OrgInvitation.org_id == org.id)
                .order_by(OrgInvitation.id.desc())
                .limit(ADMIN_LIST_CAP)
            )
        ).all()
    )
    return await _invitation_outs(session, rows)


async def invite(session: AsyncSession, user_id: int, body: InvitationCreate) -> InvitationOut:
    """Never looks the email up: the answer is the same whether an account exists. A pending,
    unexpired invitation for the same email is returned unchanged; the in-app notice goes through
    the outbox."""
    org, me = await require_org_role(session, user_id, ADMINS, lock=True)
    if body.role == ROLE_ADMIN and me.role != ROLE_OWNER:
        raise forbidden(key="orgs.roleNotAllowed")
    now = now_utc()
    pending = list(
        (
            await session.scalars(
                select(OrgInvitation).where(
                    OrgInvitation.org_id == org.id, OrgInvitation.status == INVITE_PENDING
                )
            )
        ).all()
    )
    live = []
    for inv in pending:
        if ensure_utc(inv.expires_at) <= now:
            inv.status = INVITE_EXPIRED
        elif inv.email == body.email:
            await session.commit()
            return (await _invitation_outs(session, [inv]))[0]
        else:
            live.append(inv)
    limit = get_settings().org_max_pending_invitations
    if len(live) >= limit:
        raise conflict(key="orgs.invitationQuota", params={"max": limit})
    inv = OrgInvitation(
        uuid=uuid4().hex,
        org_id=org.id,
        email=body.email,
        role=body.role,
        remark=body.remark or None,
        invited_by=user_id,
        expires_at=now + INVITATION_TTL,
    )
    session.add(inv)
    await session.flush()
    enqueue(session, INVITE_NOTIFY_TASK, {"invitation_id": inv.id})
    await session.commit()
    await session.refresh(inv)
    logger.info("org_invited", org_uuid=org.uuid, invitation_uuid=inv.uuid)
    return (await _invitation_outs(session, [inv]))[0]


async def revoke_invitation(session: AsyncSession, user_id: int, uuid: str) -> None:
    org, _ = await require_org_role(session, user_id, ADMINS, lock=True)
    inv = await session.scalar(
        select(OrgInvitation)
        .where(OrgInvitation.uuid == uuid, OrgInvitation.org_id == org.id)
        .with_for_update()
    )
    if inv is None:
        raise not_found(key="orgs.invitationNotFound")
    status = _invitation_status(inv)
    if status != INVITE_PENDING:
        raise conflict(key="orgs.invitationNotPending", params={"status": status})
    inv.status = INVITE_REVOKED
    inv.responded_at = now_utc()
    await session.commit()


@outbox_handler(INVITE_NOTIFY_TASK)
async def handle_invite_notify(session: AsyncSession, task: OutboxTask) -> None:
    """Resolve the invited email to a verified active account and notify it in-app (deduplicated
    per invitation); nothing happens without such an account."""
    inv = await session.get(OrgInvitation, task.payload["invitation_id"])
    if inv is None or _invitation_status(inv) != INVITE_PENDING:
        return
    user_id = await account_service.active_user_id_by_verified_email(session, inv.email)
    org = await session.get(Organization, inv.org_id)
    if user_id is None or org is None:
        return
    await notify_service.notify(
        session,
        user_id,
        type_="org",
        title=server_copy("orgs.invited.title", org=org.name),
        content=server_copy("orgs.invited.content", org=org.name),
        dedup_key=f"org_invite:{inv.uuid}",
    )
    await session.commit()


async def received_invitations(session: AsyncSession, user: "User") -> list[ReceivedInvitationOut]:
    """Pending invitations addressed to the caller's verified email."""
    rows = await _received_rows(session, user)
    emails = await account_service.emails_by_user(session, {inv.invited_by for inv, _ in rows})
    blocked = "member_of_org" if await membership_of(session, user.id) is not None else None
    return [
        ReceivedInvitationOut.model_validate(
            {
                "uuid": inv.uuid,
                "org_name": org.name,
                "role": inv.role,
                "invited_by": mask_handle(emails.get(inv.invited_by)),
                "created_at": inv.created_at,
                "expires_at": inv.expires_at,
                "blocked": blocked,
            }
        )
        for inv, org in rows
    ]


async def _received(session: AsyncSession, user: "User", uuid: str) -> OrgInvitation:
    for inv, _ in await _received_rows(session, user):
        if inv.uuid == uuid:
            return inv
    raise not_found(key="orgs.invitationNotFound")


async def accept_invitation(
    session: AsyncSession, user: "User", uuid: str, consent_version: str
) -> MyOrgOut:
    """Join under the organization lock; consent_version must be the current disclosure."""
    found = await _received(session, user, uuid)
    if consent_version != CONSENT_VERSION:
        raise conflict(key="orgs.consentOutdated")
    org = await _lock_org(session, found.org_id)
    inv = await session.get(OrgInvitation, found.id, with_for_update=True, populate_existing=True)
    if inv is None or _invitation_status(inv) != INVITE_PENDING or org.status != ORG_ACTIVE:
        raise conflict(key="orgs.invitationNotPending", params={"status": "revoked"})
    if await membership_of(session, user.id) is not None:
        raise conflict(key="orgs.alreadyMember")
    limit = get_settings().org_max_members
    if await _member_count(session, org.id) >= limit:
        raise conflict(key="orgs.memberQuota", params={"max": limit})
    session.add(
        OrgMember(
            org_id=org.id,
            user_id=user.id,
            role=inv.role,
            remark=inv.remark,
            invited_by=inv.invited_by,
            consent_version=consent_version,
        )
    )
    inv.status = INVITE_ACCEPTED
    inv.responded_at = now_utc()
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise conflict(key="orgs.alreadyMember") from None
    await session.commit()
    logger.info("org_joined", org_uuid=org.uuid, user_id=user.id)
    return await my_org(session, user)


async def decline_invitation(session: AsyncSession, user: "User", uuid: str) -> None:
    found = await _received(session, user, uuid)
    inv = await session.get(OrgInvitation, found.id, with_for_update=True, populate_existing=True)
    if inv is None or _invitation_status(inv) != INVITE_PENDING:
        raise conflict(key="orgs.invitationNotPending", params={"status": "revoked"})
    inv.status = INVITE_DECLINED
    inv.responded_at = now_utc()
    await session.commit()


# ---------------------------------------------------------------- finance (read only)


def _est_hours(available: Decimal, burn: Decimal) -> float | None:
    if burn <= 0:
        return None
    if available <= 0:
        return 0.0
    return round(float(available / burn), 1)


async def finance(session: AsyncSession, user_id: int) -> OrgFinanceOut:
    """Per-member available balance, bills since max(billing month start, joined_at), hourly burn
    of running on-demand / spot instances and the hours it lasts; owners and admins only."""
    org, _ = await require_org_role(session, user_id, ADMINS)
    members = await _members(session, org.id)
    ids = [m.user_id for m in members]
    period = current_billing_period()
    start, end = billing_period_range(period)
    since = {m.user_id: max(start, ensure_utc(m.joined_at)) for m in members}
    available = await billing_service.available_by_user(session, ids)
    billed = await billing_service.billed_by_user_since(session, since, end)
    running = await orchestrator_queries.running_cost_by_user(session, ids)
    emails = await account_service.emails_by_user(session, ids)
    zero = Decimal("0.00")
    rows: list[MemberFinanceOut] = []
    for m in members:
        avail = available.get(m.user_id, zero)
        burn, count = running.get(m.user_id, (zero, 0))
        rows.append(
            MemberFinanceOut.model_validate(
                {
                    "member_id": m.id,
                    "role": m.role,
                    "label": _label(m.remark, emails.get(m.user_id)),
                    "available_balance": avail,
                    "month_to_date": billed.get(m.user_id, zero),
                    "window_start": since[m.user_id],
                    "burn_per_hour": as_price(burn),
                    "running_instances": count,
                    "est_hours_left": _est_hours(avail, burn),
                }
            )
        )
    return OrgFinanceOut(
        currency=platform_currency(),
        billing_timezone=get_settings().billing_timezone,
        period=period,
        period_start=start,
        generated_at=now_utc(),
        members=rows,
        totals=FinanceTotalsOut(
            available_balance=sum((r.available_balance for r in rows), zero),
            month_to_date=sum((r.month_to_date for r in rows), zero),
            burn_per_hour=as_price(sum((r.burn_per_hour for r in rows), zero)),
            running_instances=sum(r.running_instances for r in rows),
        ),
    )


# ---------------------------------------------------------------- platform admin


async def _admin_outs(session: AsyncSession, orgs: list[Organization]) -> list[AdminOrgOut]:
    ids = [o.id for o in orgs]
    if not ids:
        return []
    owners = dict(
        (
            await session.execute(
                select(OrgMember.org_id, OrgMember.user_id).where(
                    OrgMember.org_id.in_(ids), OrgMember.role == ROLE_OWNER
                )
            )
        )
        .tuples()
        .all()
    )
    counts = dict(
        (
            await session.execute(
                select(OrgMember.org_id, func.count())
                .where(OrgMember.org_id.in_(ids))
                .group_by(OrgMember.org_id)
            )
        )
        .tuples()
        .all()
    )
    pending = dict(
        (
            await session.execute(
                select(OrgInvitation.org_id, func.count())
                .where(
                    OrgInvitation.org_id.in_(ids),
                    OrgInvitation.status == INVITE_PENDING,
                    OrgInvitation.expires_at > now_utc(),
                )
                .group_by(OrgInvitation.org_id)
            )
        )
        .tuples()
        .all()
    )
    emails = await account_service.emails_by_user(session, owners.values())
    return [
        AdminOrgOut(
            uuid=o.uuid,
            name=o.name,
            status=o.status,
            owner_user_id=owners.get(o.id),
            owner_email_masked=(mask_handle(emails.get(owners[o.id])) if o.id in owners else None),
            members=counts.get(o.id, 0),
            pending_invitations=pending.get(o.id, 0),
            created_at=o.created_at,
            dissolved_at=o.dissolved_at,
        )
        for o in orgs
    ]


async def admin_list_orgs(
    session: AsyncSession,
    *,
    q: str | None,
    status: str | None,
    cursor: str | None,
    limit: int | None,
) -> RawPage[AdminOrgOut]:
    """Newest first. q = organization uuid (32 hex), a member's user id (digits) or a name
    fragment."""
    stmt = select(Organization).order_by(Organization.id.desc())
    if status:
        stmt = stmt.where(Organization.status == status)
    term = (q or "").strip()
    if term:
        if len(term) == 32 and all(c in "0123456789abcdef" for c in term):
            stmt = stmt.where(Organization.uuid == term)
        elif term.isdigit() and len(term) <= 9:
            stmt = stmt.where(
                Organization.id.in_(select(OrgMember.org_id).where(OrgMember.user_id == int(term)))
            )
        else:
            stmt = stmt.where(Organization.name.ilike(f"%{like_escape(term)}%", escape="\\"))
    rows, next_cursor = await paginate_by_id(
        session, stmt, id_col=Organization.id, cursor=cursor, limit=limit
    )
    return RawPage(items=await _admin_outs(session, rows), next_cursor=next_cursor)


async def _org_by_uuid(session: AsyncSession, uuid: str, *, lock: bool = False) -> Organization:
    stmt = select(Organization).where(Organization.uuid == uuid)
    if lock:
        stmt = stmt.with_for_update()
    org = await session.scalar(stmt)
    if org is None:
        raise not_found(key="orgs.notFound")
    return org


async def admin_org_detail(session: AsyncSession, uuid: str) -> AdminOrgDetailOut:
    org = await _org_by_uuid(session, uuid)
    (base,) = await _admin_outs(session, [org])
    members = await _members(session, org.id)
    member_ids = [m.user_id for m in members]
    emails = await account_service.emails_by_user(session, member_ids)
    statuses = await account_service.user_statuses(session, member_ids)
    invitations = list(
        (
            await session.scalars(
                select(OrgInvitation)
                .where(OrgInvitation.org_id == org.id)
                .order_by(OrgInvitation.id.desc())
                .limit(ADMIN_LIST_CAP)
            )
        ).all()
    )
    return AdminOrgDetailOut(
        **base.model_dump(),
        member_list=[
            AdminOrgMemberOut.model_validate(
                {
                    "user_id": m.user_id,
                    "role": m.role,
                    "email_masked": mask_handle(emails.get(m.user_id)),
                    "status": statuses.get(m.user_id, "deleted"),
                    "joined_at": m.joined_at,
                }
            )
            for m in members
        ],
        invitations=[
            AdminOrgInvitationOut.model_validate(
                {
                    "uuid": i.uuid,
                    "email_masked": mask_handle(i.email),
                    "role": i.role,
                    "status": _invitation_status(i),
                    "created_at": i.created_at,
                    "expires_at": i.expires_at,
                }
            )
            for i in invitations
        ],
    )


async def _active_org_by_uuid(session: AsyncSession, uuid: str) -> Organization:
    org = await _org_by_uuid(session, uuid, lock=True)
    if org.status != ORG_ACTIVE:
        raise conflict(key="orgs.dissolved")
    return org


async def admin_dissolve(session: AsyncSession, uuid: str) -> None:
    org = await _active_org_by_uuid(session, uuid)
    members = await _dissolve(session, org)
    await _notify(session, [m.user_id for m in members], "dissolved", org.name)
    await session.commit()
    logger.info("org_dissolved_by_admin", org_uuid=org.uuid)


async def _member_by_user(session: AsyncSession, org_id: int, user_id: int) -> OrgMember:
    member = await session.scalar(
        select(OrgMember).where(OrgMember.org_id == org_id, OrgMember.user_id == user_id)
    )
    if member is None:
        raise not_found(key="orgs.memberNotFound")
    return member


async def admin_remove_member(session: AsyncSession, uuid: str, user_id: int) -> None:
    org = await _active_org_by_uuid(session, uuid)
    target = await _member_by_user(session, org.id, user_id)
    if target.role == ROLE_OWNER:
        raise conflict(key="orgs.cannotRemoveOwner")
    await _remove(session, org, [target])
    await _notify(session, [target.user_id], "removed", org.name)
    await session.commit()


async def admin_transfer(session: AsyncSession, uuid: str, user_id: int) -> None:
    org = await _active_org_by_uuid(session, uuid)
    target = await _member_by_user(session, org.id, user_id)
    if target.role == ROLE_OWNER:
        raise conflict(key="orgs.transferTargetInvalid")
    owner = await session.scalar(
        select(OrgMember).where(OrgMember.org_id == org.id, OrgMember.role == ROLE_OWNER)
    )
    if owner is None:
        target.role = ROLE_OWNER
        await session.flush()
    else:
        await _swap_owner(session, owner, target)
    await _notify(session, [target.user_id], "owner_transferred", org.name)
    await session.commit()
    logger.info("org_owner_transferred_by_admin", org_uuid=org.uuid, user_id=user_id)
