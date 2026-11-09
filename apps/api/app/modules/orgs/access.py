"""Membership and role checks shared by the organization service and its storage mounts."""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import forbidden, not_found
from app.modules.orgs.models import Organization, OrgMember


async def membership_of(session: AsyncSession, user_id: int) -> OrgMember | None:
    """The user's membership; only active organizations have members."""
    return await session.scalar(select(OrgMember).where(OrgMember.user_id == user_id))


async def lock_org(session: AsyncSession, org_id: int) -> Organization:
    """FOR UPDATE on the organization row (the first lock of every organization-scoped write)."""
    org = await session.get(Organization, org_id, with_for_update=True, populate_existing=True)
    if org is None:
        raise not_found(key="orgs.notFound")
    return org


async def require_org_role(
    session: AsyncSession, user_id: int, roles: Iterable[str], *, lock: bool = False
) -> tuple[Organization, OrgMember]:
    """The caller's organization and membership; not a member → 404, other role → 403. lock=True
    takes the organization row lock first and re-reads the membership under it."""
    member = await membership_of(session, user_id)
    if member is None:
        raise not_found(key="orgs.notMember")
    if lock:
        org = await lock_org(session, member.org_id)
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
