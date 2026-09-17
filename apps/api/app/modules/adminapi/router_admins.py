"""Admin routes (admin account CRUD)."""

from fastapi import APIRouter, Request

from app.core.audit import set_audit_target
from app.core.db import DbSession
from app.modules.adminapi import auth_service
from app.modules.adminapi.deps import CurrentAdmin, require_roles
from app.modules.adminapi.schemas import (
    AdminAccountOut,
    AdminCreateRequest,
    AdminResetPasswordRequest,
    AdminUpdateRequest,
)

router = APIRouter(tags=["admin"])


@router.get("/admins", dependencies=[require_roles()])
async def admin_list_admins(session: DbSession) -> list[AdminAccountOut]:
    return [AdminAccountOut.model_validate(a) for a in await auth_service.list_admins(session)]


@router.post("/admins", dependencies=[require_roles()], status_code=201)
async def admin_create_admin(
    body: AdminCreateRequest, session: DbSession, request: Request
) -> AdminAccountOut:
    created = await auth_service.create_admin(session, body.username, body.password, body.role)
    set_audit_target(
        request,
        f"admin:{created.id}",
        detail={"username": created.username, "role": created.role, "reason": body.reason},
    )
    return AdminAccountOut.model_validate(created)


@router.patch("/admins/{admin_id}", dependencies=[require_roles()])
async def admin_update_admin(
    admin_id: int,
    body: AdminUpdateRequest,
    admin: CurrentAdmin,
    session: DbSession,
    request: Request,
) -> AdminAccountOut:
    """Change role / deactivate. Deactivation takes effect at once (deps compares token_version per
    request)."""
    updated, before = await auth_service.update_admin(
        session, admin_id, role=body.role, new_status=body.status, actor_id=admin.id
    )
    set_audit_target(
        request,
        f"admin:{admin_id}",
        detail={
            "before": before,
            "after": body.model_dump(exclude_unset=True, exclude={"reason"}, mode="json"),
            "reason": body.reason,
        },
    )
    return AdminAccountOut.model_validate(updated)


@router.post("/admins/{admin_id}/reset-password", dependencies=[require_roles()])
async def admin_reset_password(
    admin_id: int, body: AdminResetPasswordRequest, session: DbSession, request: Request
) -> AdminAccountOut:
    updated = await auth_service.reset_admin_password(session, admin_id, body.password)
    set_audit_target(
        request, f"admin:{admin_id}", detail={"action": "reset_password", "reason": body.reason}
    )
    return AdminAccountOut.model_validate(updated)
