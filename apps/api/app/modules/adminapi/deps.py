"""管理端鉴权:独立 audience 的 JWT + 角色检查。"""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.audit import AuditActor
from app.core.db import DbSession
from app.core.errors import forbidden, unauthorized
from app.core.security import decode_token
from app.modules.adminapi.models import AdminUser

_bearer = HTTPBearer(auto_error=False)


async def get_current_admin(
    request: Request,
    session: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> AdminUser:
    if credentials is None:
        raise unauthorized()
    payload = decode_token(credentials.credentials, "admin")
    admin = await session.get(AdminUser, int(payload["sub"]))
    if admin is None or admin.status != "active":
        raise unauthorized()
    request.state.audit_actor = AuditActor("admin", str(admin.id))
    return admin


CurrentAdmin = Annotated[AdminUser, Depends(get_current_admin)]


def require_roles(*roles: str):
    """角色门:admin 恒许;其余按白名单。"""

    async def checker(
        admin: Annotated[AdminUser, Depends(get_current_admin)],
    ) -> AdminUser:
        if admin.role != "admin" and admin.role not in roles:
            raise forbidden(f"需要角色:{'/'.join(roles)}")
        return admin

    return Depends(checker)
