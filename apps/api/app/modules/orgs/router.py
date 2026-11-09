from fastapi import APIRouter, Request, Response, status

from app.core.audit import mark_audited_read, set_audit_target
from app.core.db import DbSession
from app.core.http import mark_idempotent_replay
from app.core.params import IdempotencyKey
from app.core.ratelimit import check_rate_limit
from app.modules.account import service as account_service
from app.modules.account.deps import CurrentUser
from app.modules.orgs import mounts, service, transfers, users
from app.modules.orgs.schemas import (
    BalanceTransferCreate,
    BalanceTransferOut,
    DisclosureOut,
    InvitationAccept,
    InvitationCreate,
    InvitationOut,
    ManagedPasswordOut,
    ManagedPasswordReset,
    ManagedUserCreatedOut,
    ManagedUsersCreate,
    ManagedUserUpdate,
    MemberOut,
    MemberUpdate,
    MyOrgOut,
    OrgCreate,
    OrgDissolve,
    OrgFinanceOut,
    OrgMountCreate,
    OrgMountOut,
    OrgMountUpdate,
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
    """Owner only: name and / or code (the code changes only while the tenant has created no
    accounts)."""
    await _write_limit(user.id)
    org = await service.rename_org(session, user.id, body)
    set_audit_target(request, f"org:{org.uuid}", detail=body.model_dump(exclude_none=True))
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


@router.get("/mounts")
async def list_mounts(user: CurrentUser, session: DbSession) -> list[OrgMountOut]:
    """The organization's storage mounts, every member: mounted at /mnt/org/<name> in each
    member's instances from their next start. Path, key user and check errors are shown to
    owners and admins only; the key never comes back."""
    return await mounts.list_mounts(session, user.id)


@router.post("/mounts", status_code=status.HTTP_201_CREATED)
async def create_mount(
    body: OrgMountCreate, user: CurrentUser, session: DbSession, request: Request
) -> OrgMountOut:
    """Owners and admins: a directory of the platform's Ceph cluster plus the organization's own
    cephx key. Checked in user space first; only a ready mount reaches instances. Members are
    notified."""
    await _write_limit(user.id)
    out = await mounts.create_mount(session, user.id, body)
    set_audit_target(
        request,
        f"org-mount:{out.uuid}",
        detail={"name": body.name, "read_only": body.read_only, "ceph_user": body.ceph_user},
    )
    return out


@router.patch("/mounts/{uuid}")
async def update_mount(
    uuid: str, body: OrgMountUpdate, user: CurrentUser, session: DbSession, request: Request
) -> OrgMountOut:
    """Owners and admins; a new path, key user, key or access is checked again and reaches
    instances from their next start (running instances keep the previous one)."""
    await _write_limit(user.id)
    out = await mounts.update_mount(session, user.id, uuid, body)
    set_audit_target(request, f"org-mount:{uuid}", detail={"fields": sorted(body.model_fields_set)})
    return out


@router.post("/mounts/{uuid}/check")
async def recheck_mount(
    uuid: str, user: CurrentUser, session: DbSession, request: Request
) -> OrgMountOut:
    """Owners and admins: check the directory and key again."""
    await _write_limit(user.id)
    out = await mounts.recheck_mount(session, user.id, uuid)
    set_audit_target(request, f"org-mount:{uuid}")
    return out


@router.delete("/mounts/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mount(
    uuid: str, user: CurrentUser, session: DbSession, request: Request
) -> Response:
    """Owners and admins; instances lose the mount at their next start."""
    await _write_limit(user.id)
    await mounts.delete_mount(session, user.id, uuid)
    set_audit_target(request, f"org-mount:{uuid}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_users(
    body: ManagedUsersCreate, user: CurrentUser, session: DbSession, request: Request
) -> list[ManagedUserCreatedOut]:
    """Owners and admins: create 1-100 accounts named <tenant code>.<name>, members from the
    start, no identity verification needed. Each password (the shared one, or a random one per
    account) is returned only here; the accounts pick their own at first sign-in."""
    await check_rate_limit(f"org-users:{user.id}", max_attempts=30, window_seconds=3600.0)
    out = await users.create_users(session, user.id, body)
    set_audit_target(
        request,
        f"org-users:{user.id}",
        detail={"count": len(out), "usernames": [u.username for u in out]},
    )
    return out


@router.post("/members/{member_id}/password")
async def reset_member_password(
    member_id: int,
    body: ManagedPasswordReset,
    user: CurrentUser,
    session: DbSession,
    request: Request,
) -> ManagedPasswordOut:
    """An account the tenant created (owner: any, admin: members): set or generate a password,
    end its sessions; returned only here."""
    await _write_limit(user.id)
    out = await users.reset_password(session, user.id, member_id, body)
    set_audit_target(request, f"org-member:{member_id}", detail={"action": "password_reset"})
    return out


@router.patch("/members/{member_id}/login")
async def set_member_login(
    member_id: int,
    body: ManagedUserUpdate,
    user: CurrentUser,
    session: DbSession,
    request: Request,
) -> MemberOut:
    """Switch sign-in of an account the tenant created; disabling ends its sessions at once
    (instances keep running)."""
    await _write_limit(user.id)
    out = await users.set_login(session, user.id, member_id, body)
    set_audit_target(
        request, f"org-member:{member_id}", detail={"login_disabled": body.login_disabled}
    )
    return out


@router.delete("/members/{member_id}/account", status_code=status.HTTP_204_NO_CONTENT)
async def delete_member_account(
    member_id: int, user: CurrentUser, session: DbSession, request: Request
) -> Response:
    """Delete an account the tenant created: it must hold no unreleased instance, no data disk
    and no balance (take the balance back first)."""
    await _write_limit(user.id)
    await users.delete_user(session, user.id, member_id)
    set_audit_target(request, f"org-member:{member_id}", detail={"action": "account_deleted"})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/members/{member_id}/balance")
async def move_member_balance(
    member_id: int,
    body: BalanceTransferCreate,
    user: CurrentUser,
    session: DbSession,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> BalanceTransferOut:
    """Owners and admins: allocate from the caller's available balance to a member, or take back
    from an account the tenant created. Moved money can only be spent: never refunded or
    invoiced by whoever receives it. A replay of the same Idempotency-Key returns the first move
    (200 + X-Idempotent-Replay)."""
    await check_rate_limit(f"org-balance:{user.id}", max_attempts=120, window_seconds=3600.0)
    out, created = await transfers.transfer(session, user.id, member_id, body, idempotency_key)
    if not created:
        mark_idempotent_replay(response)
    set_audit_target(
        request,
        f"org-transfer:{out.uuid}",
        detail={"member_id": member_id, "kind": body.kind, "amount": body.amount},
    )
    return out


@router.get("/transfers")
async def list_transfers(user: CurrentUser, session: DbSession) -> list[BalanceTransferOut]:
    """The tenant's balance moves, newest 200 (owners and admins)."""
    return await transfers.list_transfers(session, user.id)
