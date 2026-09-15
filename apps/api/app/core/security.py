"""密码哈希(bcrypt)与 JWT。用户端与管理端 audience 隔离,token 不可互用。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import cache
from typing import Annotated, Any, Literal
from uuid import uuid4

import bcrypt
import jwt
from pydantic import AfterValidator, Field

from app.core.config import get_settings
from app.core.errors import unauthorized
from app.core.timeutil import now_utc

TokenScope = Literal["user", "admin"]
TokenType = Literal["access", "refresh", "mfa_setup", "mfa_ticket"]

PASSWORD_MAX_BYTES = 72


def check_password_bytes(plain: str) -> str:
    """拒绝 UTF-8 编码超过 PASSWORD_MAX_BYTES 的口令,否则原样返回。"""
    if len(plain.encode()) > PASSWORD_MAX_BYTES:
        raise ValueError("密码过长:UTF-8 编码后不得超过 72 字节")
    return plain


PasswordStr = Annotated[
    str, Field(min_length=12, max_length=128), AfterValidator(check_password_bytes)
]


def hash_password_sync(plain: str) -> str:
    """同步版本;请求路径用异步版。cost 取 settings.bcrypt_rounds。"""
    salt = bcrypt.gensalt(rounds=get_settings().bcrypt_rounds)
    return bcrypt.hashpw(plain.encode(), salt).decode()


@cache
def dummy_password_hash() -> str:
    """惰性生成并缓存占位口令哈希,使用当前配置的 bcrypt cost。"""
    return hash_password_sync("dummy-timing-equalizer")


def verify_password_sync(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


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
    """签发 HS256 JWT;非 access 类型默认使用 refresh TTL,mfa_* 调用方须显式传短 TTL。

    extra 可覆盖标准 claims,只允许传入受信任的数据。
    """
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
    """验证签名、audience、issuer、时间及 typ;leeway_seconds 为时间校验宽限,失败抛 401。"""
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
