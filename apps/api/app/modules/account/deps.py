"""鉴权依赖:Bearer token → User。同时把 actor 写入 request.state 供审计中间件。"""

from typing import Annotated

from fastapi import Depends, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.audit import AuditActor
from app.core.db import DbSession
from app.core.errors import AppError, ErrorCode, unauthorized
from app.core.security import decode_token
from app.modules.account.models import User

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    session: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> User:
    if credentials is None:
        raise unauthorized()
    payload = decode_token(credentials.credentials, "user")
    user = await session.get(User, int(payload["sub"]))
    if user is None:
        raise unauthorized()
    if user.status == "deleted":
        # 已注销:全部在外凭证一律 401(含 token_version 尚未推进前签发的旧 token)
        raise AppError(
            ErrorCode.UNAUTHORIZED,
            key="account.accountDeleted",
            http_status=status.HTTP_401_UNAUTHORIZED,
        )
    if user.status == "frozen":
        raise AppError(
            ErrorCode.FORBIDDEN, key="account.userFrozen", http_status=status.HTTP_403_FORBIDDEN
        )
    if payload.get("ver", 0) != user.token_version:
        raise unauthorized()  # 已被撤销(冻结期版本推进/refresh 重放触发)
    request.state.audit_actor = AuditActor("user", str(user.id))
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
