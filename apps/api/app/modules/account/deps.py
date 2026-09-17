"""Auth dependencies: Bearer token → User; the actor is written to request.state for the audit
middleware."""

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
    """Validate the user access token and account status; a missing or mismatching version is
    rejected, success sets the audit actor."""
    if credentials is None:
        raise unauthorized()
    payload = decode_token(credentials.credentials, "user")
    user = await session.get(User, int(payload["sub"]))
    if user is None:
        raise unauthorized()
    if user.status == "deleted":
        raise AppError(
            ErrorCode.UNAUTHORIZED,
            key="account.accountDeleted",
            http_status=status.HTTP_401_UNAUTHORIZED,
        )
    if user.status == "frozen":
        raise AppError(
            ErrorCode.FORBIDDEN, key="account.userFrozen", http_status=status.HTTP_403_FORBIDDEN
        )
    if payload.get("ver") != user.token_version:
        raise unauthorized()
    request.state.audit_actor = AuditActor("user", str(user.id))
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
