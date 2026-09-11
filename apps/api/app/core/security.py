"""密码哈希(bcrypt)与 JWT。用户端与管理端 audience 隔离,token 不可互用。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import cache
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

# bcrypt 只认前 72 字节,口令须按字节数再拦一道
PASSWORD_MAX_BYTES = 72


def check_password_bytes(plain: str) -> None:
    """哈希前按字节数拦截超长口令。"""
    if len(plain.encode()) > PASSWORD_MAX_BYTES:
        raise ValueError("密码过长:UTF-8 编码后不得超过 72 字节")


def hash_password_sync(plain: str) -> str:
    """同步版本;请求路径用异步版。cost 取 settings.bcrypt_rounds。"""
    salt = bcrypt.gensalt(rounds=get_settings().bcrypt_rounds)
    return bcrypt.hashpw(plain.encode(), salt).decode()


@cache
def dummy_password_hash() -> str:
    """不存在的账号也走一次哈希校验(拉平时序);惰性生成,cost 与真实哈希一致。"""
    return hash_password_sync("dummy-timing-equalizer")


def verify_password_sync(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


# bcrypt 专属线程池(固定小上限),不与默认执行器共用:渠道 SDK 等阻塞调用打满默认池时登录不受牵连
_BCRYPT_MAX_PARALLEL = 4
_BCRYPT_EXECUTOR = ThreadPoolExecutor(max_workers=_BCRYPT_MAX_PARALLEL, thread_name_prefix="bcrypt")


async def hash_password(plain: str) -> str:
    return await asyncio.get_running_loop().run_in_executor(
        _BCRYPT_EXECUTOR, hash_password_sync, plain
    )


async def verify_password(plain: str, hashed: str) -> bool:
    return await asyncio.get_running_loop().run_in_executor(
        _BCRYPT_EXECUTOR, verify_password_sync, plain, hashed
    )


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
    """签发 JWT。jti/iat 仅 refresh 宽限重放路径传入;ttl_seconds 仅 mfa_* 短票传入。"""
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
    """leeway_seconds:exp 校验宽限(管理端续期用)。"""
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
