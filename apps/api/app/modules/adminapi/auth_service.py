"""管理员认证、MFA、账号管理与 PII 明文读取授权。"""

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
from app.core.loginguard import LoginBucket, login_failed, login_preflight, login_succeeded
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
    """PII 明文出口须调用:拒绝 readonly,校验必填事由并返回去除首尾空白的事由。"""
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
    """返回四层失败计数桶;成功只清配对桶与账号短窗,账号日窗不参与预检。"""
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
    """密码校验 → (响应, 账号)。admin_mfa_enabled 开启:未绑定发绑定票、已绑定发验证票;
    关闭:直接签发 access token(status=ok)。"""
    admin = (
        await session.execute(select(AdminUser).where(AdminUser.username == username))
    ).scalar_one_or_none()
    buckets = _login_buckets(client_ip, username)
    await login_preflight(buckets)
    password_ok = await verify_password(
        password, admin.password_hash if admin else dummy_password_hash()
    )
    if admin is None or not password_ok:
        await login_failed(buckets)
        LOGIN_FAILED_TOTAL.labels(actor_type="admin").inc()
        logger.warning("admin_login_failed", username=username, ip=client_ip)
        raise AppError(ErrorCode.LOGIN_FAILED, key="adminapi.loginFailed")
    if admin.status != "active":
        raise AppError(
            ErrorCode.USER_FROZEN,
            key="adminapi.userDisabled",
            http_status=status.HTTP_403_FORBIDDEN,
        )
    await login_succeeded(buckets)
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
    """在 RENEW_GRACE_SECONDS 宽限内续发管理员 access token。

    会话上限按 sess_iat(缺失时 iat)计算;超限、非 active 或版本不匹配时回 401。
    """
    payload = decode_token(token, "admin", leeway_seconds=RENEW_GRACE_SECONDS)
    session_iat = int(payload.get("sess_iat") or payload["iat"])
    issued_at = datetime.fromtimestamp(session_iat, tz=UTC)
    if now_utc() - issued_at > timedelta(seconds=SESSION_MAX_SECONDS):
        raise unauthorized("会话已达 12 小时上限,请重新登录")
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
    """校验短票并加载账号。票据无效/账号状态或版本已变 → MFA_TICKET_INVALID。"""
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
    """消耗一次 MFA 窗口配额。成功也计。"""
    await check_rate_limit(
        f"admin-mfa:{admin_id}", max_attempts=MFA_MAX_ATTEMPTS, window_seconds=MFA_WINDOW_SECONDS
    )


def _decrypt_totp_secret(admin: AdminUser) -> str:
    assert admin.totp_secret is not None
    return decrypt_str(admin.totp_secret, aad=f"totp:{admin.id}")


def _match_totp_timestep(secret: str, code: str, *, window: int = 1) -> int | None:
    """手动窗口匹配:返回匹配的 timestep(30s 步长),不匹配返回 None。"""
    totp = pyotp.TOTP(secret)
    now_step = int(time.time() // 30)
    for offset in range(-window, window + 1):
        step = now_step + offset
        if totp.at(step * 30) == code:
            return step
    return None


def _accept_totp_step(locked: AdminUser, matched_step: int) -> bool:
    """防重放闸(行锁内调用):matched_step 必须大于已通过的最大步,通过则单调推进。"""
    last = locked.last_totp_timestep
    if last is not None and matched_step <= last:
        return False
    locked.last_totp_timestep = matched_step
    return True


def _gen_plain_recovery_codes() -> list[str]:
    """生成 RECOVERY_CODE_COUNT 个 40 bit 恢复码;持久化时必须存哈希。"""
    return [f"{(raw := secrets.token_hex(5))[:5]}-{raw[5:]}" for _ in range(RECOVERY_CODE_COUNT)]


async def _hash_recovery_codes(plain: list[str]) -> list[str]:
    return list(await asyncio.gather(*(hash_password(code) for code in plain)))


async def _consume_recovery_code(admin: AdminUser, code: str) -> bool:
    """比对并移除匹配的恢复码哈希;调用方须持管理员行锁并提交。"""
    hashes = list(admin.totp_recovery or [])
    if not hashes:
        return False
    results = await asyncio.gather(*(verify_password(code, h) for h in hashes))
    if not any(results):
        return False
    admin.totp_recovery = [h for h, ok in zip(hashes, results, strict=True) if not ok]
    return True


async def begin_totp_setup(session: AsyncSession, ticket: str) -> tuple[str, str]:
    """重读并锁定管理员行,创建或复用未确认的 TOTP 密钥,提交后返回密钥与 otpauth URI。

    已绑定时回滚并抛 MFA_TICKET_INVALID。
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
    uri = pyotp.TOTP(secret).provisioning_uri(name=locked.username, issuer_name="SuperDL 管理端")
    return secret, uri


async def confirm_totp_setup(
    session: AsyncSession, ticket: str, code: str
) -> tuple[str, AdminUser, list[str]]:
    """重读并锁定管理员行,校验动态码并推进防重放步,启用 TOTP 后提交。

    成功时递增 token_version 撤销旧会话,返回新 access token、账号及仅本次可见的恢复码。
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
        title="管理员完成二要素(TOTP)绑定",
        content=(
            f"管理员 {locked.username} 完成了 TOTP 绑定。若非本人操作:立即由另一位超管"
            "重置其 MFA 并改密排查口令泄漏。"
        ),
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
    """重读并锁定管理员行,验证并消费 TOTP 步或恢复码后提交。

    返回 (access token, admin, 剩余恢复码数);使用 TOTP 时剩余数为 None。
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
    """重新生成恢复码(旧的全作废)。仅已绑定账号;明文仅本次返回。"""
    if not admin.totp_enabled:
        raise AppError(ErrorCode.MFA_NOT_BOUND, key="adminapi.mfaNotBound")
    plain = _gen_plain_recovery_codes()
    admin.totp_recovery = await _hash_recovery_codes(plain)
    await session.commit()
    logger.info("mfa_recovery_regenerated", admin_id=admin.id)
    return plain


async def reset_totp(session: AsyncSession, actor: AdminUser, target_id: int) -> AdminUser:
    """重置他人 TOTP 绑定与恢复码、撤销会话并提交;调用方须校验超管权限。"""
    if actor.id == target_id:
        raise AppError(
            ErrorCode.MFA_RESET_SELF_FORBIDDEN,
            key="adminapi.mfaResetSelfForbidden",
            http_status=status.HTTP_409_CONFLICT,
        )
    target = await session.get(AdminUser, target_id)
    if target is None:
        raise not_found("管理员不存在")
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
    """管理员表为空时校验引导口令长度并创建 admin 账号。"""
    existing = (await session.execute(select(AdminUser).limit(1))).scalar_one_or_none()
    if existing is not None:
        return
    if len(password) < PASSWORD_MIN_LENGTH or len(password.encode()) > PASSWORD_MAX_BYTES:
        raise RuntimeError(
            f"引导口令不合规:须 ≥{PASSWORD_MIN_LENGTH} 字符且 UTF-8 编码后 "
            f"≤{PASSWORD_MAX_BYTES} 字节,请修正 SUPERDL_SEED_ADMIN_PASSWORD"
        )
    await create_admin(session, "admin", password, "admin")
    logger.warning(
        "bootstrap_admin_created",
        username="admin",
        hint="首个管理员已创建;请立即登录改密并建出第二个 admin(调账双人复核需要两个人)",
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
    """持行锁更新角色或状态并提交;变更时撤销会话,返回账号与供审计使用的旧值。"""
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
    """服务端登出:token_version+1(行锁内),已签发的 access token 全失效。"""
    admin = await _get_admin(session, admin_id)
    admin.token_version += 1
    await session.commit()
