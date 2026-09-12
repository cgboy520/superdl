import asyncio
import secrets
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

import pyotp
from fastapi import status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_str, encrypt_str
from app.core.errors import AppError, ErrorCode, conflict, not_found, unauthorized
from app.core.idempotency import request_fingerprint
from app.core.logging import get_logger, mask_phone_value
from app.core.loginguard import LoginBucket, login_failed, login_preflight, login_succeeded
from app.core.metrics import (
    ADMIN_PRIVILEGE_CHANGE_TOTAL,
    AUTHZ_DENIED_TOTAL,
    LOGIN_FAILED_TOTAL,
    PAYMENT_REVERSAL_RESOLVED_TOTAL,
)
from app.core.money import as_amount, money_str
from app.core.pagination import Page, paginate_by_id
from app.core.platform_config import get_runtime_config
from app.core.ratelimit import check_rate_limit, ensure_not_rate_limited
from app.core.security import (
    PASSWORD_MAX_BYTES,
    check_password_bytes,
    create_token,
    decode_token,
    dummy_password_hash,
    hash_password,
    verify_password,
)
from app.core.timeutil import ensure_utc, now_utc
from app.modules.account import service as account_service
from app.modules.adminapi.models import AdminAdjustment, AdminUser
from app.modules.adminapi.schemas import (
    AdjustmentOut,
    AdminLoginTokenOut,
    AdminOut,
    MfaChallengeOut,
)
from app.modules.billing import service as billing_service
from app.modules.nodes import service as nodes_service
from app.modules.notify import service as notify_service
from app.modules.orchestrator import queries as orchestrator_queries
from app.modules.orchestrator.schemas import NON_TERMINAL_STATUSES

logger = get_logger(__name__)

LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300.0
# 纯 IP 桶(只计失败)
LOGIN_IP_MAX_ATTEMPTS = 30
LOGIN_IP_WINDOW_SECONDS = 3600.0
# 纯账号桶:15 分钟窗成功即清零,日窗只计失败不清零
LOGIN_ACCT_MAX_ATTEMPTS = 10
LOGIN_ACCT_WINDOW_SECONDS = 900.0
LOGIN_ACCT_DAILY_MAX_ATTEMPTS = 30
LOGIN_ACCT_DAILY_WINDOW_SECONDS = 86400.0

# 与 AdminCreateRequest.password 的 min_length 对齐(引导口令不经 schema)
PASSWORD_MIN_LENGTH = 12

# ---------- TOTP MFA(admin_mfa_enabled,默认开,全部管理角色) ----------
# 开关只有全员开/全员关两档。开启时登录只签发挑战票,
# 正式 token 经 confirm_totp_setup / verify_mfa_login 签发
MFA_SETUP_TICKET_SECONDS = 600  # 绑定票 10 分钟,一次性(typ=mfa_setup)
MFA_VERIFY_TICKET_SECONDS = 300  # 二要素票 5 分钟
MFA_MAX_ATTEMPTS = 5  # 同账号 5 次/10min
MFA_WINDOW_SECONDS = 600.0
RECOVERY_CODE_COUNT = 10

# 单笔调账绝对值上限:超出走线下流程
ADJUST_MAX_ABS = Decimal("100000.00")

# 明文 PII 事由的最短长度
REVEAL_REASON_MIN_LENGTH = 2


def ensure_reveal_allowed(*, role: str, reason: str | None) -> str:
    """PII 明文读取的统一开闸(租户实名 / 发票抬头邮箱共用)。返回规范化后的事由。

    readonly 永不给明文;事由必填。所有明文出口都必须过这里。
    """
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


def _check_password_bytes(password: str) -> None:
    """哈希前按字节数拦截超长口令(bcrypt 5.x 限 72 字节)。"""
    try:
        check_password_bytes(password)
    except ValueError:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="common.validation") from None


def _login_buckets(client_ip: str | None, username: str) -> list[LoginBucket]:
    """四层登录桶,全部只计失败。IP 桶与日桶不清零;日窗账号桶不进 bcrypt 前的准入预检
    (只在失败后计数)。每次调用重读阈值常量(测试可 monkeypatch)。"""
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
    # 已封禁的桶在 bcrypt 之前拦下;日窗账号桶不进预检(见 _login_buckets)
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
    # 两步验证关闭:密码即登录
    cfg = await get_runtime_config(session)
    if not cfg.admin_mfa_enabled:
        token = create_token(
            str(admin.id), "admin", token_type="access", extra={"ver": admin.token_version}
        )
        return AdminLoginTokenOut(
            status="ok", access_token=token, admin=AdminOut.model_validate(admin)
        ), admin
    # 未绑定 → 绑定票(10min);已绑定 → 二要素票(5min)
    if admin.totp_enabled:
        return MfaChallengeOut(status="mfa_required", ticket=_mfa_ticket(admin, setup=False)), admin
    return MfaChallengeOut(status="mfa_setup", ticket=_mfa_ticket(admin, setup=True)), admin


# 静默续期:access 过期后 15 分钟宽限内可换发;自首次登录(sess_iat)起 12 小时绝对上限
RENEW_GRACE_SECONDS = 15 * 60
SESSION_MAX_SECONDS = 12 * 3600


async def renew_access_token(session: AsyncSession, token: str) -> str:
    """有效或刚过期(宽限内)的管理端 access token 换发新 token。
    token_version 变或超绝对会话上限即 401。"""
    payload = decode_token(token, "admin", leeway_seconds=RENEW_GRACE_SECONDS)
    # 绝对上限锚定首次登录时刻(sess_iat)
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


# ---------- TOTP 票据与校验 ----------
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
    """10 个 XXXXX-XXXXX 恢复码(40 bit/个)。落库只有 bcrypt 哈希。"""
    return [f"{(raw := secrets.token_hex(5))[:5]}-{raw[5:]}" for _ in range(RECOVERY_CODE_COUNT)]


async def _hash_recovery_codes(plain: list[str]) -> list[str]:
    """并发受 _bcrypt_permits(4)约束。"""
    return list(await asyncio.gather(*(hash_password(code) for code in plain)))


async def _consume_recovery_code(admin: AdminUser, code: str) -> bool:
    """匹配即作废。bcrypt 逐个比对,并发受信号量约束。"""
    hashes = list(admin.totp_recovery or [])
    if not hashes:
        return False
    results = await asyncio.gather(*(verify_password(code, h) for h in hashes))
    if not any(results):
        return False
    admin.totp_recovery = [h for h, ok in zip(hashes, results, strict=True) if not ok]
    return True


async def begin_totp_setup(session: AsyncSession, ticket: str) -> tuple[str, str]:
    """生成(或复用进行中的)TOTP 密钥,返回 (secret, otpauth_uri)。
    行锁下读改写;已绑定即拒(MFA_TICKET_INVALID);确认绑定前 totp_enabled 恒为 false。
    """
    admin = await _admin_from_ticket(session, ticket, expected="mfa_setup")
    # 与 confirm/verify 同一配额桶
    await _check_mfa_rate(admin.id)
    # populate_existing 不可省:该行已在 identity map,须强制重读加锁后的值
    locked = await session.get(AdminUser, admin.id, with_for_update=True, populate_existing=True)
    assert locked is not None
    if locked.totp_enabled:
        await session.rollback()  # 锁不留到请求结束
        raise AppError(ErrorCode.MFA_TICKET_INVALID, key="adminapi.mfaTicketInvalid")
    if locked.totp_secret is None:
        secret = pyotp.random_base32()
        locked.totp_secret = encrypt_str(secret, aad=f"totp:{locked.id}")
    else:
        secret = _decrypt_totp_secret(locked)
    await session.commit()  # 未改也要提交,锁随事务释放
    uri = pyotp.TOTP(secret).provisioning_uri(name=locked.username, issuer_name="SuperDL 管理端")
    return secret, uri


async def confirm_totp_setup(
    session: AsyncSession, ticket: str, code: str
) -> tuple[str, AdminUser, list[str]]:
    """校验首个动态码 → 启用 + 发恢复码(明文仅本次) → 签发正式 token。
    绑定成功即 token_version+1(绑定票作废,踢掉其它在外会话)。
    """
    admin = await _admin_from_ticket(session, ticket, expected="mfa_setup")
    await _check_mfa_rate(admin.id)
    if admin.totp_secret is None:
        raise AppError(ErrorCode.MFA_TICKET_INVALID, key="adminapi.mfaTicketInvalid")
    # 行锁内匹配 + 防重放推进
    locked = await session.get(AdminUser, admin.id, with_for_update=True, populate_existing=True)
    assert locked is not None
    matched = _match_totp_timestep(_decrypt_totp_secret(locked), code)
    if matched is None or not _accept_totp_step(locked, matched):
        await _count_mfa_attempt(admin.id)
        logger.warning("mfa_bind_failed", admin_id=admin.id)
        raise AppError(ErrorCode.MFA_CODE_INVALID, key="adminapi.mfaCodeInvalid")
    await _count_mfa_attempt(admin.id)  # 成功也计配额
    if locked.totp_enabled:
        # 并发两张票同时 confirm 时后到者不得重发恢复码
        raise AppError(ErrorCode.MFA_TICKET_INVALID, key="adminapi.mfaTicketInvalid")
    plain = _gen_plain_recovery_codes()
    locked.totp_recovery = await _hash_recovery_codes(plain)
    locked.totp_enabled = True
    locked.token_version += 1  # 绑定票即刻作废;access token 用 bump 后的版本
    # 绑定即告警(管理端告警流)

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
    """二要素验证:6 位 TOTP,或恢复码(用后作废)。返回 (token, admin, 剩余恢复码数)。"""
    admin = await _admin_from_ticket(session, ticket, expected="mfa_ticket")
    await _check_mfa_rate(admin.id)
    # 行锁内验证:timestep 推进/恢复码作废与「是否已用」判定原子化
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
    await _count_mfa_attempt(admin.id)  # 成功也计配额
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
    """超管为他人重置 TOTP:清空绑定与恢复码并踢掉全部会话,下次登录重新绑定。本人不可自重置。"""
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
    _check_password_bytes(password)
    admin = AdminUser(username=username, password_hash=await hash_password(password), role=role)
    session.add(admin)
    try:
        await session.commit()
    except IntegrityError as exc:  # username 唯一
        await session.rollback()
        raise conflict(key="adminapi.adminUsernameTaken") from exc
    await session.refresh(admin)
    ADMIN_PRIVILEGE_CHANGE_TOTAL.inc()
    return admin


async def ensure_bootstrap_admin(session: AsyncSession, password: str) -> None:
    """首个管理员引导(scripts/seed_dev.py 调用):表为空时创建 admin 账号。"""
    existing = (await session.execute(select(AdminUser).limit(1))).scalar_one_or_none()
    if existing is not None:
        return
    # 引导口令不经请求 schema,长度须与 AdminCreateRequest 同标准
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
    """改角色/停用。返回 (账号, 旧值快照),旧值交调用方落审计。"""
    admin = await _get_admin(session, admin_id)
    if admin.id == actor_id and (new_status == "disabled" or (role and role != admin.role)):
        # 禁止自停用/自降权
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
        # 角色变更/停用即失效已签发的 token
        admin.token_version += 1
    await session.commit()
    await session.refresh(admin)
    if before:
        ADMIN_PRIVILEGE_CHANGE_TOTAL.inc()
    return admin, before


async def reset_admin_password(session: AsyncSession, admin_id: int, password: str) -> AdminUser:
    admin = await _get_admin(session, admin_id)
    _check_password_bytes(password)
    admin.password_hash = await hash_password(password)
    admin.token_version += 1  # 改密即踢掉全部在外会话
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
    _check_password_bytes(new_password)
    admin.password_hash = await hash_password(new_password)
    admin.token_version += 1
    await session.commit()


async def logout(session: AsyncSession, admin_id: int) -> None:
    """服务端登出:token_version+1(行锁内),已签发的 access token 全失效。"""
    admin = await _get_admin(session, admin_id)
    admin.token_version += 1
    await session.commit()


async def create_adjustment(
    session: AsyncSession,
    *,
    user_id: int,
    amount,
    reason: str,
    created_by: int,
    idempotency_key: str | None = None,
) -> tuple["AdminAdjustment", bool]:
    """发起调账。返回 (调账单, created):created=False = 幂等重放,路由回 200 + 重放区分头。
    幂等键作用域为 (发起人,租户,键);同键重放比对请求体指纹,不一致 409。"""
    amount = as_amount(Decimal(str(amount)))
    fingerprint = request_fingerprint(user_id, amount, reason)

    if idempotency_key:
        # 幂等归属为 (created_by, user_id) 双列,不可用 find_replay(仅支持单列)
        existing = (
            await session.execute(
                select(AdminAdjustment).where(
                    AdminAdjustment.created_by == created_by,
                    AdminAdjustment.user_id == user_id,
                    AdminAdjustment.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise conflict(key="common.idempotencyKeyMismatch")
            return existing, False  # 幂等重放

    await account_service.get_user(session, user_id)
    if amount == 0:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="adminapi.adjustNotZero")
    if abs(amount) > ADJUST_MAX_ABS:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="common.validation",
            detail={"field": "amount", "max_abs": money_str(ADJUST_MAX_ABS)},
        )
    adj = AdminAdjustment(
        user_id=user_id,
        amount=amount,
        reason=reason,
        created_by=created_by,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
    )
    session.add(adj)
    # 并发同键撞 uq_admin_adjustments_idem_scope 由唯一约束兜底(500),不回查
    await session.commit()
    await session.refresh(adj)
    return adj, True


async def review_adjustment(
    session: AsyncSession,
    adjustment_id: int,
    *,
    approve: bool,
    reviewer_id: int,
    comment: str | None,
    audit_writer: Callable[[AsyncSession], Awaitable[None]] | None = None,
):
    """双人复核:复核人不得是发起人,且须为调账发起前已存在的账号;通过即生效(钱包+流水,同事务)。
    audit_writer 在 approve 分支 commit 前调用,写失败即整体回滚。"""
    # 行锁:后到者看到非 pending 即 409
    adj = await session.get(AdminAdjustment, adjustment_id, with_for_update=True)
    if adj is None:
        raise not_found()
    if adj.status != "pending":
        raise conflict(key="adminapi.adjustAlreadyProcessed")
    if adj.created_by == reviewer_id:
        raise AppError(
            ErrorCode.ADMIN_SECOND_REVIEW_REQUIRED,
            key="adminapi.adjustSecondReviewer",
            http_status=403,
        )
    reviewer = await session.get(AdminUser, reviewer_id)
    if reviewer is None or ensure_utc(reviewer.created_at) >= ensure_utc(adj.created_at):
        # 发起后才创建的账号不构成独立的第二人
        raise AppError(
            ErrorCode.ADMIN_SECOND_REVIEW_REQUIRED,
            key="adminapi.adjustReviewerTooNew",
            http_status=403,
        )
    adj.reviewed_by = reviewer_id
    adj.review_comment = comment
    adj.reviewed_at = now_utc()
    if not approve:
        adj.status = "rejected"
        await session.commit()
        return adj
    adj.status = "approved"
    if adj.amount > 0:
        await billing_service.credit(
            session,
            adj.user_id,
            adj.amount,
            type_="adjust",
            ref_type="adjustment",
            ref_id=str(adj.id),
            remark=f"调账:{adj.reason}",
        )
    else:
        await billing_service.debit(
            session,
            adj.user_id,
            -adj.amount,
            type_="adjust",
            ref_type="adjustment",
            ref_id=str(adj.id),
            remark=f"调账:{adj.reason}",
            allow_negative=True,
        )
    if audit_writer is not None:
        await audit_writer(session)  # 与生效同事务
    await session.commit()
    return adj


async def resolve_reversal(
    session: AsyncSession,
    order_no: str,
    *,
    action: Literal["release", "chargeback"],
    reason: str,
    operator_id: int,
    audit_writer: Callable[[AsyncSession], Awaitable[None]] | None = None,
) -> None:
    """核销渠道冲正(channel_reversed 分桶的唯一出口)。

    - release:解冻等额冻结额,订单恢复退款资格;
    - chargeback:解冻 + 等额扣减(ledger adjust,允许透支)。
    两种都只写 resolved_at + action,不清 channel_reversed_at(同一通知重放不再二次冻结)。
    单操作人 + 同步审计 + 计数(release 条条告警 PaymentReversalReleased)。
    """
    order = (
        await session.execute(
            select(billing_service.Order)
            .where(billing_service.Order.order_no == order_no)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if order is None:
        raise not_found("订单不存在")
    if not billing_service.reversal_pending(order):
        raise conflict(key="adminapi.reversalNotPending")
    await billing_service.release_freeze(session, order.user_id, order.amount)
    order.channel_reversal_resolved_at = now_utc()
    order.channel_reversal_action = action
    if action != "release":
        await billing_service.debit(
            session,
            order.user_id,
            order.amount,
            type_="adjust",
            ref_type="reversal",
            ref_id=order.order_no,
            remark=f"渠道冲正核销:{reason}",
            allow_negative=True,  # 核销后余额为负走欠费链路
        )
    if audit_writer is not None:
        await audit_writer(session)  # 与核销同事务
    await session.commit()
    PAYMENT_REVERSAL_RESOLVED_TOTAL.labels(action=action).inc()
    logger.info(
        "reversal_resolved",
        order_no=order_no,
        action=action,
        operator_id=operator_id,
        amount=str(order.amount),
    )


async def list_adjustments(
    session: AsyncSession,
    *,
    status: str | None = None,
    user_id: int | None = None,
    day_range: tuple[datetime, datetime] | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> Page[AdjustmentOut]:
    """调账单列表(游标分页,降序)。status/user_id 精确;day_range 按 created_at 过滤。"""
    stmt = select(AdminAdjustment).order_by(AdminAdjustment.id.desc())
    if status:
        stmt = stmt.where(AdminAdjustment.status == status)
    if user_id is not None:
        stmt = stmt.where(AdminAdjustment.user_id == user_id)
    if day_range is not None:
        stmt = stmt.where(
            AdminAdjustment.created_at >= day_range[0], AdminAdjustment.created_at < day_range[1]
        )
    page_items, next_cursor = await paginate_by_id(
        session, stmt, id_col=AdminAdjustment.id, cursor=cursor, limit=limit
    )
    return Page[AdjustmentOut](
        items=[
            AdjustmentOut(
                id=r.id,
                user_id=r.user_id,
                amount=money_str(r.amount),
                reason=r.reason,
                status=r.status,
                created_by=r.created_by,
                reviewed_by=r.reviewed_by,
                review_comment=r.review_comment,
                created_at=r.created_at.isoformat(),
            )
            for r in page_items
        ],
        next_cursor=next_cursor,
    )


# ---------- 只读聚合(总览/调账上下文/改价影响面) ----------


async def overview(session: AsyncSession) -> dict[str, Any]:
    """运营总览聚合:精确 COUNT 口径。

    实例分状态计数不含 released;付费租户 = ledger consume 全表聚合;租户总数取 active 用户;
    包周期在保数 = 未到期的订阅行数;节点/GPU 取台账全量(含 NotReady/Missing),
    竞价占用按台账 gpu_used 截断。
    """
    counted = await orchestrator_queries.count_instances_by_status(session)
    status_counts: dict[str, int] = {st: counted.get(st, 0) for st in NON_TERMINAL_STATUSES}

    consumed = await billing_service.consumed_by_user(session)
    active_user_ids = await account_service.list_active_user_ids(session)

    pools: dict[str, dict[str, int]] = {}
    nodes_ready = nodes_missing = 0
    specs = await nodes_service.list_node_specs(session)
    spot_by_pool = await orchestrator_queries.running_spot_gpus_by_pool(session)
    for r in specs:
        if r.status == "Ready":
            nodes_ready += 1
        elif r.status == "Missing":
            nodes_missing += 1
        pool = pools.setdefault(r.pool_label or "", {"gpu_total": 0, "gpu_used": 0, "ready": 0})
        pool["gpu_total"] += r.gpu_count
        pool["gpu_used"] += r.gpu_used
        if r.status == "Ready":
            pool["ready"] += r.gpu_count

    return {
        "instances_by_status": status_counts,
        "tenants_total": len(active_user_ids),
        "paying_tenants": sum(1 for v in consumed.values() if v > 0),
        "subscriptions_active": len(
            await billing_service.reserved_subscription_instance_ids(session)
        ),
        "nodes_total": len(specs),
        "nodes_ready": nodes_ready,
        "nodes_missing": nodes_missing,
        "pools": [
            {
                "pool": name or "unlabeled",
                "gpu_total": p["gpu_total"],
                "gpu_used": p["gpu_used"],
                "gpu_spot_used": min(spot_by_pool.get(name, 0), p["gpu_used"]),
                "ready_gpu_total": p["ready"],
            }
            for name, p in sorted(pools.items())
        ],
    }


async def adjust_context(session: AsyncSession, user_id: int) -> dict[str, Any]:
    """调账前置上下文(只读):租户身份 + 当前余额 + 近 3 条流水。用户不存在 → 404。"""
    user = await account_service.get_user(session, user_id)
    balance = await billing_service.get_balance(session, user_id)
    recent = await billing_service.ledger_page(session, user_id, limit=3)
    running_by_user = await orchestrator_queries.list_running_instances_by_user(session)
    return {
        "user_id": user.id,
        "phone_masked": mask_phone_value(user.phone),
        "status": user.status,
        "balance": money_str(balance),
        "running_instances": len(running_by_user.get(user_id, [])),
        "recent_ledger": recent.items,
    }


async def sku_impact(session: AsyncSession, sku_id: int) -> dict[str, Any]:
    """改价影响面(只读):该 SKU 当前活跃(creating/starting/running)实例数/用户数/卡数。"""
    active = []
    for st in ("creating", "starting", "running"):
        active.extend(await orchestrator_queries.list_instances_by_status(session, st))
    mine = [i for i in active if i.sku_id == sku_id]
    return {
        "sku_id": sku_id,
        "active_instances": len(mine),
        "active_users": len({i.user_id for i in mine}),
        "active_gpus": sum(i.gpu_count for i in mine),
    }
