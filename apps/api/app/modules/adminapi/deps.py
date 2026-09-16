"""Admin auth: JWT with its own audience + role checks."""

from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.audit import AuditActor
from app.core.db import DbSession
from app.core.errors import forbidden, unauthorized
from app.core.metrics import AUTHZ_DENIED_TOTAL
from app.core.security import decode_token
from app.modules.adminapi.models import AdminUser

_bearer = HTTPBearer(auto_error=False)


async def get_current_admin(
    request: Request,
    session: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> AdminUser:
    """Validate the admin access token and active status; a missing or mismatching version is
    rejected, success sets the audit actor."""
    if credentials is None:
        raise unauthorized()
    payload = decode_token(credentials.credentials, "admin")
    admin = await session.get(AdminUser, int(payload["sub"]))
    if admin is None or admin.status != "active":
        raise unauthorized()
    if payload.get("ver") != admin.token_version:
        raise unauthorized()
    request.state.audit_actor = AuditActor("admin", str(admin.id))
    return admin


CurrentAdmin = Annotated[AdminUser, Depends(get_current_admin)]


def require_roles(*roles: str) -> Any:
    """Role gate: admin always passes; others by allow-list. No arguments = admin only."""

    async def checker(
        admin: Annotated[AdminUser, Depends(get_current_admin)],
    ) -> AdminUser:
        if admin.role != "admin" and admin.role not in roles:
            AUTHZ_DENIED_TOTAL.labels(actor_type="admin").inc()
            if roles:
                raise forbidden(key="adminapi.roleRequired", params={"roles": "/".join(roles)})
            raise forbidden(key="adminapi.roleRequiredAdmin")
        return admin

    return Depends(checker)
