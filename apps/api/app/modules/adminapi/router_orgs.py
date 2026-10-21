"""Admin routes (organizations)."""

from fastapi import APIRouter, Request

from app.core.audit import mark_audited_read, set_audit_target
from app.core.db import DbSession
from app.core.pagination import Page
from app.core.params import Cursor, Limit
from app.modules.adminapi.deps import require_roles
from app.modules.adminapi.schemas import ReasonBody
from app.modules.orgs import service as orgs_service
from app.modules.orgs.schemas import AdminOrgDetailOut, AdminOrgOut

router = APIRouter(tags=["admin"])


class OrgUserReason(ReasonBody):
    user_id: int


@router.get("/orgs", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_list_orgs(
    session: DbSession,
    request: Request,
    q: str | None = None,
    status: str | None = None,
    cursor: str | None = Cursor,
    limit: int | None = Limit,
) -> Page[AdminOrgOut]:
    """Organizations, newest first (cursor paged). q = organization uuid, a member's user id or a
    name fragment; every search writes an audit row."""
    if q:
        mark_audited_read(request, "org-search", detail={"query_len": len(q)})
    page = await orgs_service.admin_list_orgs(
        session, q=q, status=status, cursor=cursor, limit=limit
    )
    return Page[AdminOrgOut](items=page.items, next_cursor=page.next_cursor)


@router.get("/orgs/{uuid}", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_get_org(uuid: str, session: DbSession, request: Request) -> AdminOrgDetailOut:
    """Members (masked handles) and invitations (masked emails)."""
    mark_audited_read(request, f"org:{uuid}")
    return await orgs_service.admin_org_detail(session, uuid)


@router.post("/orgs/{uuid}/dissolve", dependencies=[require_roles("ops")])
async def admin_dissolve_org(
    uuid: str, body: ReasonBody, session: DbSession, request: Request
) -> AdminOrgDetailOut:
    """End every membership and pending invitation; the organization's gateways are deleted."""
    await orgs_service.admin_dissolve(session, uuid)
    set_audit_target(request, f"org:{uuid}", detail={"reason": body.reason})
    return await orgs_service.admin_org_detail(session, uuid)


@router.post("/orgs/{uuid}/members/remove", dependencies=[require_roles("ops")])
async def admin_remove_org_member(
    uuid: str, body: OrgUserReason, session: DbSession, request: Request
) -> AdminOrgDetailOut:
    """Remove a member other than the owner."""
    await orgs_service.admin_remove_member(session, uuid, body.user_id)
    set_audit_target(
        request, f"org:{uuid}", detail={"reason": body.reason, "user_id": body.user_id}
    )
    return await orgs_service.admin_org_detail(session, uuid)


@router.post("/orgs/{uuid}/transfer", dependencies=[require_roles()])
async def admin_transfer_org(
    uuid: str, body: OrgUserReason, session: DbSession, request: Request
) -> AdminOrgDetailOut:
    """Make a member the owner (the previous owner becomes an admin): ownership grants sight of
    members' balances, so admin role only."""
    await orgs_service.admin_transfer(session, uuid, body.user_id)
    set_audit_target(
        request, f"org:{uuid}", detail={"reason": body.reason, "user_id": body.user_id}
    )
    return await orgs_service.admin_org_detail(session, uuid)
