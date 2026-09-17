"""Verification codes for email and phone handles: the gated send chain and the consume step.

Gate order on send: per-target precheck bucket → CAPTCHA (when enabled) → per-IP bucket →
per-target backoff (advisory lock) → per-target daily cap → platform budget of the channel.
Codes are stored as keyed digests only; a delivery failure voids the row.
"""

import math
import secrets
from datetime import timedelta
from typing import Literal

from fastapi import status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.captcha import CaptchaError, get_captcha_channel
from app.core.config import get_settings
from app.core.crypto import hash_verification_code, hash_verification_code_candidates
from app.core.email import EmailError, ensure_email_platform_quota, get_email_channel
from app.core.errors import AppError, ErrorCode
from app.core.handles import Handle, ratelimit_key
from app.core.locale import DEFAULT_LOCALE, Locale
from app.core.logging import get_logger, mask_handle
from app.core.metrics import VERIFICATION_SENT_TOTAL
from app.core.platform_config import get_runtime_config
from app.core.ratelimit import check_rate_limit
from app.core.sms import SmsError, ensure_sms_platform_quota, get_sms_channel
from app.core.timeutil import ensure_utc, now_utc
from app.core.verification import code_email
from app.modules.account.models import VerificationCode

logger = get_logger(__name__)

CodePurpose = Literal["register", "login", "reset_password", "bind_handle"]

MOCK_CODE = "123456"
MAX_CODE_ATTEMPTS = 5
SEND_BACKOFF_MAX_EXPONENT = 3
CONSUME_DAILY_MAX = 10
SEND_IP_HOURLY_MAX = 20
SEND_TARGET_DAILY_MAX = 15
PRECHECK_TARGET_HOURLY_MAX = 30


def channel_of(handle: Handle) -> Literal["email", "sms"]:
    return "email" if handle.kind == "email" else "sms"


async def _verify_captcha(
    session: AsyncSession, captcha_token: str | None, client_ip: str | None
) -> None:
    """With captcha_enabled: missing token → 400, channel failure → 502, rejected → 400."""
    if not captcha_token:
        raise AppError(ErrorCode.CAPTCHA_REQUIRED, key="account.captchaRequired")
    try:
        channel = await get_captcha_channel(session)
        captcha_ok = await channel.verify(captcha_token, client_ip=client_ip)
    except CaptchaError as exc:
        logger.error("captcha_channel_error", error=str(exc))
        raise AppError(
            ErrorCode.CAPTCHA_CHANNEL_ERROR,
            key="account.captchaChannelError",
            http_status=status.HTTP_502_BAD_GATEWAY,
        ) from exc
    if not captcha_ok:
        raise AppError(ErrorCode.CAPTCHA_VERIFY_FAILED, key="account.captchaVerifyFailed")


async def _enforce_send_backoff(session: AsyncSession, handle: Handle) -> None:
    """Refuse when the newest code for this target is younger than the base interval; N unconsumed
    codes in a row (voided / expired included) raise the interval to base × 2^(N-1), capped at
    SEND_BACKOFF_MAX_EXPONENT. The caller holds the target's advisory lock."""
    channel = channel_of(handle)
    recent = list(
        (
            await session.execute(
                select(VerificationCode)
                .where(
                    VerificationCode.channel == channel,
                    VerificationCode.target == handle.value,
                    VerificationCode.created_at > now_utc() - timedelta(hours=24),
                )
                .order_by(VerificationCode.id.desc())
                .limit(16)
            )
        ).scalars()
    )
    if not recent:
        return
    streak = 0
    for row in recent:
        if row.consumed_at is not None:
            break
        streak += 1
    base = get_settings().sms_send_interval_seconds
    required = base * (2 ** min(max(streak, 1) - 1, SEND_BACKOFF_MAX_EXPONENT))
    elapsed = (now_utc() - ensure_utc(recent[0].created_at)).total_seconds()
    if elapsed < required:
        raise AppError(
            ErrorCode.CODE_TOO_FREQUENT,
            key="account.codeTooFrequent",
            params={"seconds": math.ceil(required - elapsed)},
            http_status=status.HTTP_429_TOO_MANY_REQUESTS,
        )


async def _deliver(
    session: AsyncSession, handle: Handle, purpose: str, code: str, locale: Locale
) -> None:
    if handle.kind == "email":
        content = code_email(purpose, code, locale)
        channel = await get_email_channel(session)
        await channel.send(handle.value, content.subject, content.text, content.html)
    else:
        sms = await get_sms_channel(session)
        await sms.send(handle.value, "verify", {"code": code}, locale=locale)


async def send_code(
    session: AsyncSession,
    handle: Handle,
    purpose: CodePurpose,
    *,
    client_ip: str | None = None,
    captcha_token: str | None = None,
    locale: Locale = DEFAULT_LOCALE,
    require_captcha: bool = True,
) -> None:
    """Create a code under the target's advisory lock, commit, then deliver; a delivery failure
    voids the row and surfaces as 502 CODE_SEND_FAILED."""
    settings = get_settings()
    rk = ratelimit_key(handle.value)
    channel = channel_of(handle)
    await check_rate_limit(
        f"code-precheck:{rk}", max_attempts=PRECHECK_TARGET_HOURLY_MAX, window_seconds=3600.0
    )
    cfg = await get_runtime_config(session)
    if require_captcha and cfg.captcha_enabled:
        await _verify_captcha(session, captcha_token, client_ip)
    await check_rate_limit(
        f"code-send-ip:{client_ip or '-'}", max_attempts=SEND_IP_HOURLY_MAX, window_seconds=3600.0
    )
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"{handle.kind}:{handle.value}"},
    )
    await _enforce_send_backoff(session, handle)
    await check_rate_limit(
        f"code-send-target:{rk}", max_attempts=SEND_TARGET_DAILY_MAX, window_seconds=86400.0
    )
    if channel == "email":
        await ensure_email_platform_quota("verify")
        mocked = cfg.email_provider == "mock"
    else:
        await ensure_sms_platform_quota("verify")
        mocked = cfg.sms_provider == "mock"
    code = MOCK_CODE if mocked else f"{secrets.randbelow(10**6):06d}"
    row = VerificationCode(
        channel=channel,
        target=handle.value,
        code_hash=hash_verification_code(channel, handle.value, purpose, code),
        purpose=purpose,
        expires_at=now_utc() + timedelta(seconds=settings.sms_code_ttl_seconds),
    )
    session.add(row)
    await session.commit()
    try:
        await _deliver(session, handle, purpose, code, locale)
        VERIFICATION_SENT_TOTAL.labels(channel=channel, purpose=purpose).inc()
    except (SmsError, EmailError) as exc:
        row.used_at = now_utc()
        await session.commit()
        logger.error(
            "verification_send_failed",
            target=mask_handle(handle.value),
            channel=channel,
            error=str(exc),
        )
        raise AppError(
            ErrorCode.CODE_SEND_FAILED,
            key="account.codeSendFailed",
            http_status=status.HTTP_502_BAD_GATEWAY,
        ) from exc


async def consume_code(session: AsyncSession, handle: Handle, code: str, purpose: str) -> None:
    """Check and consume the newest live code for (channel, target, purpose) under a row lock;
    does not commit. A wrong code commits the attempt counter and raises CODE_INVALID; the row is
    voided after MAX_CODE_ATTEMPTS. Successful consumption is capped per target per day."""
    channel = channel_of(handle)
    row = (
        await session.execute(
            select(VerificationCode)
            .where(
                VerificationCode.channel == channel,
                VerificationCode.target == handle.value,
                VerificationCode.purpose == purpose,
                VerificationCode.used_at.is_(None),
                VerificationCode.expires_at > now_utc(),
            )
            .order_by(VerificationCode.id.desc())
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise AppError(ErrorCode.CODE_INVALID, key="account.codeInvalid")
    candidates = hash_verification_code_candidates(channel, handle.value, purpose, code)
    if not any(secrets.compare_digest(row.code_hash, c) for c in candidates):
        row.attempts += 1
        if row.attempts >= MAX_CODE_ATTEMPTS:
            row.used_at = now_utc()
        await session.commit()
        raise AppError(ErrorCode.CODE_INVALID, key="account.codeInvalid")
    await check_rate_limit(
        f"code-consume:{ratelimit_key(handle.value)}",
        max_attempts=CONSUME_DAILY_MAX,
        window_seconds=86400.0,
    )
    row.used_at = row.consumed_at = now_utc()
