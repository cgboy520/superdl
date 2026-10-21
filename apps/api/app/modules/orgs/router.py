from fastapi import APIRouter, Request, Response, status

from app.core.audit import mark_audited_read, set_audit_target
from app.core.db import DbSession
from app.core.http import mark_idempotent_replay
from app.core.params import IdempotencyKey
from app.core.ratelimit import check_rate_limit
from app.modules.account import service as account_service
from app.modules.account.deps import CurrentUser
from app.modules.orgs import service
from app.modules.orgs.schemas import (
    DisclosureOut,
    InvitationAccept,
    InvitationCreate,
    InvitationOut,
    MemberOut,
    MemberUpdate,
    MyOrgOut,
    OrgCreate,
    OrgDissolve,
    OrgFinanceOut,
    OrgRename,
    OrgTransfer,
    ReceivedInvitationOut,
)

router = APIRouter(prefix="/org", tags=["orgs"])


async def _write_limit(user_id: int) -> None:
    await check_rate_limit(f"org-write:{user_id}", max_attempts=60, window_seconds=3600.0)


@router.get("")
async def get_my_org(user: CurrentUser, session: DbSession) -> MyOrgOut:
    """The caller's organization and role (org null outside one) and the count of pending
    invitations addressed to the caller."""
    return await service.my_org(session, user)


@router.get("/disclosure")
async def get_disclosure(_user: CurrentUser) -> DisclosureOut:
    """What organization owners and admins can see about a member; accepting an invitation
    records this version."""
    return service.disclosure()


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_org(
    body: OrgCreate,
    user: CurrentUser,
    session: DbSession,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> MyOrgOut:
    """Create an organization owned by the caller (outside any organization only). An
    idempotency-key replay returns 200 + X-Idempotent-Replay."""
    await account_service.require_real_name_if_required(session, user, key="orgs.realNameRequired")
    await check_rate_limit(f"org-create:{user.id}", max_attempts=3, window_seconds=86400.0)
    org, created = await service.create_org(session, user.id, body, idempotency_key)
    if not created:
        mark_idempotent_replay(response)
    set_audit_target(request, f"org:{org.uuid}")
    return await service.my_org(session, user)


@router.patch("")
async def rename_org(
    body: OrgRename, user: CurrentUser, session: DbSession, request: Request
) -> MyOrgOut:
    """Owner only."""
    await _write_limit(user.id)
    org = await service.rename_org(session, user.id, body.name)
    set_audit_target(request, f"org:{org.uuid}", detail={"name": body.name})
    return await service.my_org(session, user)


@router.post("/transfer")
async def transfer_owner(
    body: OrgTransfer, user: CurrentUser, session: DbSession, request: Request
) -> MyOrgOut:
    """Owner only: the target member becomes the owner, the caller an admin."""
    await _write_limit(user.id)
    org = await service.transfer_owner(session, user.id, body.member_id)
    set_audit_target(request, f"org:{org.uuid}", detail={"member_id": body.member_id})
    return await service.my_org(session, user)


@router.post("/dissolve")
async def dissolve_org(
    body: OrgDissolve, user: CurrentUser, session: DbSession, request: Request
) -> MyOrgOut:
    """Owner only, the organization name typed again: memberships and pending invitations end,
    organization gateways are deleted. Members' accounts, balances and resources are untouched."""
    await _write_limit(user.id)
    org = await service.dissolve_org(session, user.id, body.confirm_name)
    set_audit_target(request, f"org:{org.uuid}")
    return await service.my_org(session, user)


@router.post("/leave")
async def leave_org(user: CurrentUser, session: DbSession, request: Request) -> MyOrgOut:
    """Admins and members; the owner transfers ownership or dissolves first."""
    await _write_limit(user.id)
    await service.leave_org(session, user.id)
    set_audit_target(request, f"user:{user.id}")
    return await service.my_org(session, user)


@router.get("/members")
async def list_members(user: CurrentUser, session: DbSession) -> list[MemberOut]:
    """Owners and admins."""
    return await service.list_members(session, user.id)


@router.patch("/members/{member_id}")
async def update_member(
    member_id: int, body: MemberUpdate, user: CurrentUser, session: DbSession, request: Request
) -> MemberOut:
    """Owner: role (admin / member) and remark; admin: remark only."""
    await _write_limit(user.id)
    out = await service.update_member(session, user.id, member_id, body)
    set_audit_target(
        request,
        f"org-member:{member_id}",
        detail={"fields": sorted(body.model_fields_set)},
    )
    return out


@router.delete("/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    member_id: int, user: CurrentUser, session: DbSession, request: Request
) -> Response:
    """Owner removes anyone else, an admin removes members only. The member's instances leave the
    organization's gateways in the same transaction."""
    await _write_limit(user.id)
    await service.remove_member(session, user.id, member_id)
    set_audit_target(request, f"org-member:{member_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/invitations")
async def list_invitations(user: CurrentUser, session: DbSession) -> list[InvitationOut]:
    """Sent invitations (owners and admins), newest first."""
    return await service.list_invitations(session, user.id)


@router.post("/invitations", status_code=status.HTTP_201_CREATED)
async def invite(
    body: InvitationCreate, user: CurrentUser, session: DbSession, request: Request
) -> InvitationOut:
    """Invite an email address (admins invite members, the owner also admins). The answer never
    tells whether an account exists; a pending invitation for the same address is returned
    unchanged."""
    await check_rate_limit(f"org-invite:{user.id}", max_attempts=30, window_seconds=3600.0)
    out = await service.invite(session, user.id, body)
    set_audit_target(request, f"org-invitation:{out.uuid}", detail={"role": body.role})
    return out


@router.delete("/invitations/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invitation(
    uuid: str, user: CurrentUser, session: DbSession, request: Request
) -> Response:
    await _write_limit(user.id)
    await service.revoke_invitation(session, user.id, uuid)
    set_audit_target(request, f"org-invitation:{uuid}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/invitations/received")
async def received_invitations(
    user: CurrentUser, session: DbSession
) -> list[ReceivedInvitationOut]:
    """Pending invitations addressed to the caller's verified email."""
    return await service.received_invitations(session, user)


@router.post("/invitations/received/{uuid}/accept")
async def accept_invitation(
    uuid: str, body: InvitationAccept, user: CurrentUser, session: DbSession, request: Request
) -> MyOrgOut:
    """Join the organization; consent_version must be the current disclosure version."""
    await _write_limit(user.id)
    out = await service.accept_invitation(session, user, uuid, body.consent_version)
    set_audit_target(
        request,
        f"org-invitation:{uuid}",
        detail={"consent_version": body.consent_version},
    )
    return out


@router.post("/invitations/received/{uuid}/decline", status_code=status.HTTP_204_NO_CONTENT)
async def decline_invitation(
    uuid: str, user: CurrentUser, session: DbSession, request: Request
) -> Response:
    await _write_limit(user.id)
    await service.decline_invitation(session, user, uuid)
    set_audit_target(request, f"org-invitation:{uuid}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/finance")
async def org_finance(user: CurrentUser, session: DbSession, request: Request) -> OrgFinanceOut:
    """Members' available balance, this billing month's bills since they joined, hourly burn and
    the hours it lasts (owners and admins; every read is audited)."""
    out = await service.finance(session, user.id)
    mark_audited_read(request, f"org-finance:{user.id}", detail={"members": len(out.members)})
    return out
