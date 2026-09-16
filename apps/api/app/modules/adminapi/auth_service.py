"""Admin authentication, MFA, account management and PII plaintext read authorisation."""

import asyncio
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import pyotp
from fastapi import status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_str, encrypt_str
from app.core.errors import AppError, ErrorCode, conflict, not_found, unauthorized
from app.core.logging import get_logger
from app.core.loginguard import LoginBucket, login_attempt, login_failed, login_succeeded
from app.core.metrics import (
    ADMIN_PRIVILEGE_CHANGE_TOTAL,
    AUTHZ_DENIED_TOTAL,
    LOGIN_FAILED_TOTAL,
)
from app.core.platform_config import get_runtime_config
from app.core.ratelimit import check_rate_limit, ensure_not_rate_limited
from app.core.security import (
    PASSWORD_MAX_BYTES,
    create_token,
    decode_token,
    dummy_password_hash,
    hash_password,
    verify_password,
)
from app.core.servercopy import copy as server_copy
from app.core.timeutil import now_utc
from app.modules.adminapi.models import AdminUser
from app.modules.adminapi.schemas import (
    AdminLoginTokenOut,
    AdminOut,
    MfaChallengeOut,
)
from app.modules.notify import service as notify_service

logger = get_logger(__name__)

LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300.0
LOGIN_IP_MAX_ATTEMPTS = 30
LOGIN_IP_WINDOW_SECONDS = 3600.0
LOGIN_ACCT_MAX_ATTEMPTS = 10
LOGIN_ACCT_WINDOW_SECONDS = 900.0
LOGIN_ACCT_DAILY_MAX_ATTEMPTS = 30
LOGIN_ACCT_DAILY_WINDOW_SECONDS = 86400.0

PASSWORD_MIN_LENGTH = 12

MFA_SETUP_TICKET_SECONDS = 600
MFA_VERIFY_TICKET_SECONDS = 300
MFA_MAX_ATTEMPTS = 5
MFA_WINDOW_SECONDS = 600.0
RECOVERY_CODE_COUNT = 10

REVEAL_REASON_MIN_LENGTH = 2


def ensure_reveal_allowed(*, role: str, reason: str | None) -> str:
    """Every PII plaintext outlet calls this: rejects readonly, requires a reason and returns it
    stripped."""
    if role == "readonly":
        AUTHZ_DENIED_TOTAL.labels(actor_type="admin").inc()
        raise AppError(ErrorCode.FORBIDDEN, key="common.forbidden", http_status=403)
    normalized = (reason or "").strip()
    if len(normalized) < REVEAL_REASON_MIN_LENGTH:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="common.validation",
            detail={"field": "reason", "constraint": "required_when_reveal"},
        )
    return normalized


def _login_buckets(client_ip: str | None, username: str) -> list[LoginBucket]:
    """The four buckets; the first three count before checking, reset the pair and account
    short-window buckets on success and refund the IP bucket; the account daily window counts
    failures only."""
    ip = client_ip or "-"
    return [
        LoginBucket(f"admin-login-ip:{ip}", LOGIN_IP_MAX_ATTEMPTS, LOGIN_IP_WINDOW_SECONDS),
        LoginBucket(
            f"admin-login:{ip}:{username}",
            LOGIN_MAX_ATTEMPTS,
            LOGIN_WINDOW_SECONDS,
            clear_on_success=True,
        ),
        LoginBucket(
            f"admin-login-acct:{username}",
            LOGIN_ACCT_MAX_ATTEMPTS,
            LOGIN_ACCT_WINDOW_SECONDS,
            clear_on_success=True,
        ),
        LoginBucket(
            f"admin-login-acct-daily:{username}",
            LOGIN_ACCT_DAILY_MAX_ATTEMPTS,
            LOGIN_ACCT_DAILY_WINDOW_SECONDS,
            preflight=False,
        ),
    ]


async def login(
    session: AsyncSession, username: str, password: str, *, client_ip: str | None = None
) -> tuple[MfaChallengeOut | AdminLoginTokenOut, AdminUser]:
    """Password check → (response, account). With admin_mfa_enabled: an enrolment ticket for
    unenrolled accounts, a verification ticket for enrolled ones;
    off: the access token is issued directly (status=ok)."""
    admin = (
        await session.execute(select(AdminUser).where(AdminUser.username == username))
    ).scalar_one_or_none()
    buckets = _login_buckets(client_ip, username)
    await login_attempt(buckets)
    password_ok = await verify_password(
        password, admin.password_hash if admin else dummy_password_hash()
    )
    if admin is None or not password_ok:
        await login_failed(buckets, precounted=True)
        LOGIN_FAILED_TOTAL.labels(actor_type="admin").inc()
        logger.warning("admin_login_failed", username=username, ip=client_ip)
        raise AppError(ErrorCode.LOGIN_FAILED, key="adminapi.loginFailed")
    if admin.status != "active":
        raise AppError(
            ErrorCode.USER_FROZEN,
            key="adminapi.userDisabled",
            http_status=status.HTTP_403_FORBIDDEN,
        )
    await login_succeeded(buckets, precounted=True)
    cfg = await get_runtime_config(session)
    if not cfg.admin_mfa_enabled:
        token = create_token(
            str(admin.id), "admin", token_type="access", extra={"ver": admin.token_version}
        )
        return AdminLoginTokenOut(
            status="ok", access_token=token, admin=AdminOut.model_validate(admin)
        ), admin
    if admin.totp_enabled:
        return MfaChallengeOut(status="mfa_required", ticket=_mfa_ticket(admin, setup=False)), admin
    return MfaChallengeOut(status="mfa_setup", ticket=_mfa_ticket(admin, setup=True)), admin


RENEW_GRACE_SECONDS = 15 * 60
SESSION_MAX_SECONDS = 12 * 3600


async def renew_access_token(session: AsyncSession, token: str) -> str:
    """Renew the admin access token within the RENEW_GRACE_SECONDS grace.

    The session cap is computed from sess_iat (iat when missing); over the cap, not active or a
    version mismatch → 401.
    """
    payload = decode_token(token, "admin", leeway_seconds=RENEW_GRACE_SECONDS)
    session_iat = int(payload.get("sess_iat") or payload["iat"])
    issued_at = datetime.fromtimestamp(session_iat, tz=UTC)
    if now_utc() - issued_at > timedelta(seconds=SESSION_MAX_SECONDS):
        raise unauthorized("session reached the 12-hour cap, sign in again")
    admin = await session.get(AdminUser, int(payload["sub"]))
    if admin is None or admin.status != "active" or payload.get("ver") != admin.token_version:
        raise unauthorized()
    return create_token(
        str(admin.id),
        "admin",
        token_type="access",
        extra={"ver": admin.token_version, "sess_iat": session_iat},
    )


def _mfa_ticket(admin: AdminUser, *, setup: bool) -> str:
    return create_token(
        str(admin.id),
        "admin",
        token_type="mfa_setup" if setup else "mfa_ticket",
        ttl_seconds=MFA_SETUP_TICKET_SECONDS if setup else MFA_VERIFY_TICKET_SECONDS,
        extra={"ver": admin.token_version},
    )


async def _admin_from_ticket(
    session: AsyncSession, ticket: str, *, expected: Literal["mfa_setup", "mfa_ticket"]
) -> AdminUser:
    """Verify the short ticket and load the account. Invalid ticket / changed account status or
    version → MFA_TICKET_INVALID."""
    try:
        payload = decode_token(ticket, "admin", expected_type=expected)
    except AppError as exc:
        raise AppError(ErrorCode.MFA_TICKET_INVALID, key="adminapi.mfaTicketInvalid") from exc
    admin = await session.get(AdminUser, int(payload["sub"]))
    if admin is None or admin.status != "active" or payload.get("ver") != admin.token_version:
        raise AppError(ErrorCode.MFA_TICKET_INVALID, key="adminapi.mfaTicketInvalid")
    return admin


async def _check_mfa_rate(admin_id: int) -> None:
    await ensure_not_rate_limited(
        f"admin-mfa:{admin_id}", max_attempts=MFA_MAX_ATTEMPTS, window_seconds=MFA_WINDOW_SECONDS
    )


async def _count_mfa_attempt(admin_id: int) -> None:
    """Consume one unit of the MFA window quota. Successes count too."""
    await check_rate_limit(
        f"admin-mfa:{admin_id}", max_attempts=MFA_MAX_ATTEMPTS, window_seconds=MFA_WINDOW_SECONDS
    )


def _decrypt_totp_secret(admin: AdminUser) -> str:
    assert admin.totp_secret is not None
    return decrypt_str(admin.totp_secret, aad=f"totp:{admin.id}")


def _match_totp_timestep(secret: str, code: str, *, window: int = 1) -> int | None:
    """Manual window match: returns the matching timestep (30 s steps), None when nothing
    matches."""
    totp = pyotp.TOTP(secret)
    now_step = int(time.time() // 30)
    for offset in range(-window, window + 1):
        step = now_step + offset
        if totp.at(step * 30) == code:
            return step
    return None


def _accept_totp_step(locked: AdminUser, matched_step: int) -> bool:
    """Replay guard (called under the row lock): matched_step must exceed the largest accepted step,
    then advances monotonically."""
    last = locked.last_totp_timestep
    if last is not None and matched_step <= last:
        return False
    locked.last_totp_timestep = matched_step
    return True


def _gen_plain_recovery_codes() -> list[str]:
    """Generate RECOVERY_CODE_COUNT 40-bit recovery codes; persist hashes only."""
    return [f"{(raw := secrets.token_hex(5))[:5]}-{raw[5:]}" for _ in range(RECOVERY_CODE_COUNT)]


async def _hash_recovery_codes(plain: list[str]) -> list[str]:
    return list(await asyncio.gather(*(hash_password(code) for code in plain)))


async def _consume_recovery_code(admin: AdminUser, code: str) -> bool:
    """Compare and remove the matching recovery-code hash; the caller holds the admin row lock and
    commits."""
    hashes = list(admin.totp_recovery or [])
    if not hashes:
        return False
    results = await asyncio.gather(*(verify_password(code, h) for h in hashes))
    if not any(results):
        return False
    admin.totp_recovery = [h for h, ok in zip(hashes, results, strict=True) if not ok]
    return True


async def begin_totp_setup(session: AsyncSession, ticket: str) -> tuple[str, str]:
    """Re-read and lock the admin row, create or reuse the unconfirmed TOTP secret, commit and
    return the secret and otpauth URI.

    Already enrolled rolls back and raises MFA_TICKET_INVALID.
    """
    admin = await _admin_from_ticket(session, ticket, expected="mfa_setup")
    await _check_mfa_rate(admin.id)
    locked = await session.get(AdminUser, admin.id, with_for_update=True, populate_existing=True)
    assert locked is not None
    if locked.totp_enabled:
        await session.rollback()
        raise AppError(ErrorCode.MFA_TICKET_INVALID, key="adminapi.mfaTicketInvalid")
    if locked.totp_secret is None:
        secret = pyotp.random_base32()
        locked.totp_secret = encrypt_str(secret, aad=f"totp:{locked.id}")
    else:
        secret = _decrypt_totp_secret(locked)
    await session.commit()
    uri = pyotp.TOTP(secret).provisioning_uri(name=locked.username, issuer_name="SuperDL admin")
    return secret, uri


async def confirm_totp_setup(
    session: AsyncSession, ticket: str, code: str
) -> tuple[str, AdminUser, list[str]]:
    """Re-read and lock the admin row, verify the code and advance the replay step, enable TOTP and
    commit.

    On success token_version is bumped to revoke old sessions; returns the new access token, the
    account and the recovery codes visible this once.
    """
    admin = await _admin_from_ticket(session, ticket, expected="mfa_setup")
    await _check_mfa_rate(admin.id)
    if admin.totp_secret is None:
        raise AppError(ErrorCode.MFA_TICKET_INVALID, key="adminapi.mfaTicketInvalid")
    locked = await session.get(AdminUser, admin.id, with_for_update=True, populate_existing=True)
    assert locked is not None
    matched = _match_totp_timestep(_decrypt_totp_secret(locked), code)
    if matched is None or not _accept_totp_step(locked, matched):
        await _count_mfa_attempt(admin.id)
        logger.warning("mfa_bind_failed", admin_id=admin.id)
        raise AppError(ErrorCode.MFA_CODE_INVALID, key="adminapi.mfaCodeInvalid")
    await _count_mfa_attempt(admin.id)
    if locked.totp_enabled:
        raise AppError(ErrorCode.MFA_TICKET_INVALID, key="adminapi.mfaTicketInvalid")
    plain = _gen_plain_recovery_codes()
    locked.totp_recovery = await _hash_recovery_codes(plain)
    locked.totp_enabled = True
    locked.token_version += 1
    await notify_service.notify(
        session,
        None,
        type_="admin_alert",
        title=server_copy("adminapi.mfa_bound.title"),
        content=server_copy("adminapi.mfa_bound.content", username=locked.username),
        severity="warning",
    )
    await session.commit()
    logger.info("mfa_bound", admin_id=admin.id)
    token = create_token(
        str(locked.id), "admin", token_type="access", extra={"ver": locked.token_version}
    )
    return token, locked, plain


async def verify_mfa_login(
    session: AsyncSession, ticket: str, code: str
) -> tuple[str, AdminUser, int | None]:
    """Re-read and lock the admin row, verify and consume the TOTP step or recovery code, commit.

    Returns (access token, admin, recovery codes left); None left when TOTP was used.
    """
    admin = await _admin_from_ticket(session, ticket, expected="mfa_ticket")
    await _check_mfa_rate(admin.id)
    locked = await session.get(AdminUser, admin.id, with_for_update=True, populate_existing=True)
    assert locked is not None
    ok = False
    used_recovery = False
    if code.isdigit() and len(code) == 6:
        matched = _match_totp_timestep(_decrypt_totp_secret(locked), code)
        ok = matched is not None and _accept_totp_step(locked, matched)
    else:
        used_recovery = ok = await _consume_recovery_code(locked, code.strip().lower())
    if not ok:
        await _count_mfa_attempt(admin.id)
        logger.warning("mfa_verify_failed", admin_id=admin.id)
        raise AppError(ErrorCode.MFA_CODE_INVALID, key="adminapi.mfaCodeInvalid")
    await _count_mfa_attempt(admin.id)
    await session.commit()
    if used_recovery:
        logger.info("mfa_recovery_used", admin_id=admin.id)
    token = create_token(
        str(locked.id), "admin", token_type="access", extra={"ver": locked.token_version}
    )
    left = len(locked.totp_recovery or []) if used_recovery else None
    return token, locked, left


async def regenerate_recovery_codes(session: AsyncSession, admin: AdminUser) -> list[str]:
    """Regenerate the recovery codes (all old ones void). Enrolled accounts only; plaintext returned
    this once."""
    if not admin.totp_enabled:
        raise AppError(ErrorCode.MFA_NOT_BOUND, key="adminapi.mfaNotBound")
    plain = _gen_plain_recovery_codes()
    admin.totp_recovery = await _hash_recovery_codes(plain)
    await session.commit()
    logger.info("mfa_recovery_regenerated", admin_id=admin.id)
    return plain


async def reset_totp(session: AsyncSession, actor: AdminUser, target_id: int) -> AdminUser:
    """Reset another admin's TOTP enrolment and recovery codes, revoke sessions and commit; the
    caller
    checks the admin role."""
    if actor.id == target_id:
        raise AppError(
            ErrorCode.MFA_RESET_SELF_FORBIDDEN,
            key="adminapi.mfaResetSelfForbidden",
            http_status=status.HTTP_409_CONFLICT,
        )
    target = await session.get(AdminUser, target_id)
    if target is None:
        raise not_found("administrator not found")
    target.totp_secret = None
    target.totp_enabled = False
    target.totp_recovery = None
    target.token_version += 1
    await session.commit()
    ADMIN_PRIVILEGE_CHANGE_TOTAL.inc()
    logger.warning("mfa_reset", actor_id=actor.id, target_id=target_id)
    return target


async def create_admin(session: AsyncSession, username: str, password: str, role: str) -> AdminUser:
    admin = AdminUser(username=username, password_hash=await hash_password(password), role=role)
    session.add(admin)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise conflict(key="adminapi.adminUsernameTaken") from exc
    await session.refresh(admin)
    ADMIN_PRIVILEGE_CHANGE_TOTAL.inc()
    return admin


async def ensure_bootstrap_admin(session: AsyncSession, password: str) -> None:
    """When the admin table is empty, validate the bootstrap password length and create the admin
    account."""
    existing = (await session.execute(select(AdminUser).limit(1))).scalar_one_or_none()
    if existing is not None:
        return
    if len(password) < PASSWORD_MIN_LENGTH or len(password.encode()) > PASSWORD_MAX_BYTES:
        raise RuntimeError(
            f"bootstrap password rejected: needs >= {PASSWORD_MIN_LENGTH} characters and"
            f" <= {PASSWORD_MAX_BYTES} bytes when UTF-8 encoded; fix SUPERDL_SEED_ADMIN_PASSWORD"
        )
    await create_admin(session, "admin", password, "admin")
    logger.warning(
        "bootstrap_admin_created",
        username="admin",
        hint="first admin created; sign in and change the password now, then create a second admin"
        " (adjustment review needs two people)",
    )


async def list_admins(session: AsyncSession) -> list[AdminUser]:
    return list((await session.execute(select(AdminUser).order_by(AdminUser.id))).scalars().all())


async def _get_admin(session: AsyncSession, admin_id: int) -> AdminUser:
    admin = await session.get(AdminUser, admin_id, with_for_update=True)
    if admin is None:
        raise not_found()
    return admin


async def update_admin(
    session: AsyncSession,
    admin_id: int,
    *,
    role: str | None,
    new_status: str | None,
    actor_id: int,
) -> tuple[AdminUser, dict[str, Any]]:
    """Update role or status under the row lock and commit; a change revokes sessions; returns the
    account and the old values for the audit."""
    admin = await _get_admin(session, admin_id)
    if admin.id == actor_id and (new_status == "disabled" or (role and role != admin.role)):
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="adminapi.cannotChangeSelf",
            http_status=status.HTTP_409_CONFLICT,
        )
    before: dict[str, Any] = {}
    if role is not None and role != admin.role:
        before["role"] = admin.role
        admin.role = role
    if new_status is not None and new_status != admin.status:
        before["status"] = admin.status
        admin.status = new_status
    if before:
        admin.token_version += 1
    await session.commit()
    await session.refresh(admin)
    if before:
        ADMIN_PRIVILEGE_CHANGE_TOTAL.inc()
    return admin, before


async def reset_admin_password(session: AsyncSession, admin_id: int, password: str) -> AdminUser:
    admin = await _get_admin(session, admin_id)
    admin.password_hash = await hash_password(password)
    admin.token_version += 1
    await session.commit()
    await session.refresh(admin)
    ADMIN_PRIVILEGE_CHANGE_TOTAL.inc()
    return admin


async def change_own_password(
    session: AsyncSession, admin_id: int, current_password: str, new_password: str
) -> None:
    admin = await _get_admin(session, admin_id)
    if not await verify_password(current_password, admin.password_hash):
        raise AppError(ErrorCode.LOGIN_FAILED, key="adminapi.loginFailed")
    admin.password_hash = await hash_password(new_password)
    admin.token_version += 1
    await session.commit()


async def logout(session: AsyncSession, admin_id: int) -> None:
    """Server-side logout: token_version+1 (under the row lock), every issued access token becomes
    invalid."""
    admin = await _get_admin(session, admin_id)
    admin.token_version += 1
    await session.commit()
