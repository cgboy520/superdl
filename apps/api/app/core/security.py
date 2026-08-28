"""密码哈希(bcrypt)与 JWT。用户端与管理端 audience 隔离,token 不可互用。"""

import asyncio
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

import bcrypt
import jwt

from app.core.config import get_settings
from app.core.errors import unauthorized
from app.core.timeutil import now_utc

TokenScope = Literal["user", "admin"]
# access/refresh 之外:mfa_setup(绑定票 10min)/mfa_ticket(登录二要素票 5min)
TokenType = Literal["access", "refresh", "mfa_setup", "mfa_ticket"]


def hash_password_sync(plain: str) -> str:
    """同步版本:只给模块级常量(如时序拉平用的假哈希)用,请求路径一律用异步版。"""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password_sync(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


# bcrypt 单次 ~200ms:必须出让线程池,且并发数取固定小上限(k8s limit 下 os.cpu_count
# 不可信),撞库/扫号流量排队而非并行抢 CPU,防登录接口被打成全站 DoS
_BCRYPT_MAX_PARALLEL = 4
_bcrypt_permits = asyncio.Semaphore(_BCRYPT_MAX_PARALLEL)


async def hash_password(plain: str) -> str:
    async with _bcrypt_permits:
        return await asyncio.to_thread(hash_password_sync, plain)


async def verify_password(plain: str, hashed: str) -> bool:
    async with _bcrypt_permits:
        return await asyncio.to_thread(verify_password_sync, plain, hashed)


def _audience(scope: TokenScope) -> str:
    settings = get_settings()
    return settings.jwt_user_audience if scope == "user" else settings.jwt_admin_audience


def create_token(
    subject: str,
    scope: TokenScope,
    *,
    token_type: TokenType = "access",
    extra: dict[str, Any] | None = None,
    jti: str | None = None,
    iat: datetime | None = None,
    ttl_seconds: int | None = None,
) -> str:
    """签发 JWT。jti/iat 仅由 refresh 轮换的宽限重放路径显式传入:
    同一载荷 + 同一密钥的重编码是确定性的,重放才能拿回首次签发的同一对 token。
    ttl_seconds 仅 mfa_* 短票显式传入;access/refresh 走全局配置。"""
    settings = get_settings()
    if ttl_seconds is not None:
        ttl = ttl_seconds
    else:
        ttl = (
            settings.access_token_ttl_seconds
            if token_type == "access"
            else settings.refresh_token_ttl_seconds
        )
    now = iat if iat is not None else now_utc()
    payload: dict[str, Any] = {
        "sub": subject,
        "aud": _audience(scope),
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + timedelta(seconds=ttl),
        "jti": jti if jti is not None else uuid4().hex,
        "typ": token_type,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(
    token: str,
    scope: TokenScope,
    *,
    expected_type: TokenType = "access",
    leeway_seconds: int = 0,
) -> dict[str, Any]:
    """leeway_seconds:exp 校验宽限(管理端续期用——刚过期几分钟内的 token 可换发新 token)。"""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            audience=_audience(scope),
            issuer=settings.jwt_issuer,
            leeway=timedelta(seconds=leeway_seconds),
        )
    except jwt.PyJWTError as exc:
        raise unauthorized() from exc
    if payload.get("typ") != expected_type:
        raise unauthorized("token 类型不匹配")
    return payload
