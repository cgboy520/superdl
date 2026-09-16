"""Password hashing (bcrypt) and JWT. User and admin audiences are isolated, tokens are not
interchangeable."""

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import cache
from typing import Annotated, Any, Literal
from uuid import uuid4

import bcrypt
import jwt
from pydantic import AfterValidator, Field

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode, unauthorized
from app.core.timeutil import now_utc

TokenScope = Literal["user", "admin"]
TokenType = Literal["access", "refresh", "mfa_setup", "mfa_ticket"]

PASSWORD_MAX_BYTES = 72


def check_password_bytes(plain: str) -> str:
    """Reject passwords whose UTF-8 encoding exceeds PASSWORD_MAX_BYTES, otherwise return as-is."""
    if len(plain.encode()) > PASSWORD_MAX_BYTES:
        raise ValueError("password too long: at most 72 bytes when UTF-8 encoded")
    return plain


PasswordStr = Annotated[
    str, Field(min_length=12, max_length=128), AfterValidator(check_password_bytes)
]


def hash_password_sync(plain: str) -> str:
    """Synchronous variant; request paths use the async one. cost = settings.bcrypt_rounds."""
    salt = bcrypt.gensalt(rounds=get_settings().bcrypt_rounds)
    return bcrypt.hashpw(plain.encode(), salt).decode()


@cache
def dummy_password_hash() -> str:
    """Lazily build and cache the dummy password hash with the configured bcrypt cost."""
    return hash_password_sync("dummy-timing-equalizer")


def verify_password_sync(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


_BCRYPT_MAX_PARALLEL = 4
_BCRYPT_MAX_INFLIGHT = 64
_BCRYPT_EXECUTOR = ThreadPoolExecutor(max_workers=_BCRYPT_MAX_PARALLEL, thread_name_prefix="bcrypt")
_bcrypt_inflight = 0


async def _run_bcrypt[T](fn: Callable[..., T], *args: Any) -> T:
    """Bounded queueing: more than _BCRYPT_MAX_INFLIGHT in flight (queued included) → 429 straight
    away, no unbounded queue."""
    global _bcrypt_inflight
    if _bcrypt_inflight >= _BCRYPT_MAX_INFLIGHT:
        raise AppError(
            ErrorCode.RATE_LIMITED,
            key="common.rateLimited",
            http_status=429,
            headers={"Retry-After": "1"},
        )
    _bcrypt_inflight += 1
    try:
        return await asyncio.get_running_loop().run_in_executor(_BCRYPT_EXECUTOR, fn, *args)
    finally:
        _bcrypt_inflight -= 1


async def hash_password(plain: str) -> str:
    return await _run_bcrypt(hash_password_sync, plain)


async def verify_password(plain: str, hashed: str) -> bool:
    return await _run_bcrypt(verify_password_sync, plain, hashed)


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
    """Issue an HS256 JWT; non-access types default to the refresh TTL, mfa_* callers must pass a
    short TTL explicitly.

    extra may override the standard claims; pass trusted data only.
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
    """Verify signature, audience, issuer, times and typ; leeway_seconds is the time-check leeway,
    failure raises 401."""
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
        raise unauthorized("token type mismatch")
    return payload
