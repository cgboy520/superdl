"""密码哈希(bcrypt)与 JWT。用户端与管理端 audience 隔离,token 不可互用。"""

import asyncio
from datetime import timedelta
from typing import Any, Literal
from uuid import uuid4

import bcrypt
import jwt

from app.core.config import get_settings
from app.core.errors import unauthorized
from app.core.timeutil import now_utc

TokenScope = Literal["user", "admin"]


def hash_password_sync(plain: str) -> str:
    """同步版本:只给模块级常量(如时序拉平用的假哈希)用,请求路径一律用异步版。"""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password_sync(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


# bcrypt 单次 ~200ms,出让线程池,不阻塞事件循环
async def hash_password(plain: str) -> str:
    return await asyncio.to_thread(hash_password_sync, plain)


async def verify_password(plain: str, hashed: str) -> bool:
    return await asyncio.to_thread(verify_password_sync, plain, hashed)


def _audience(scope: TokenScope) -> str:
    settings = get_settings()
    return settings.jwt_user_audience if scope == "user" else settings.jwt_admin_audience


def create_token(
    subject: str,
    scope: TokenScope,
    *,
    token_type: Literal["access", "refresh"] = "access",
    extra: dict[str, Any] | None = None,
) -> str:
    settings = get_settings()
    ttl = (
        settings.access_token_ttl_seconds
        if token_type == "access"
        else settings.refresh_token_ttl_seconds
    )
    now = now_utc()
    payload: dict[str, Any] = {
        "sub": subject,
        "aud": _audience(scope),
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + timedelta(seconds=ttl),
        "jti": uuid4().hex,
        "typ": token_type,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(
    token: str,
    scope: TokenScope,
    *,
    expected_type: Literal["access", "refresh"] = "access",
) -> dict[str, Any]:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            audience=_audience(scope),
            issuer=settings.jwt_issuer,
        )
    except jwt.PyJWTError as exc:
        raise unauthorized() from exc
    if payload.get("typ") != expected_type:
        raise unauthorized("token 类型不匹配")
    return payload
