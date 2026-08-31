from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode, conflict, not_found, unauthorized
from app.core.idempotency import request_fingerprint
from app.core.logging import get_logger, mask_phone_value
from app.core.money import money_str
from app.core.pagination import Page
from app.core.platform_config import get_effective_platform_config
from app.core.ratelimit import check_rate_limit, clear_rate_limit, ensure_not_rate_limited
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    PASSWORD_MAX_BYTES,
    check_password_bytes,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.core.timeutil import ensure_utc, now_utc
from app.modules.adminapi.models import AdminAdjustment, AdminUser
from app.modules.adminapi.schemas import (
    AdjustmentOut,
    AdminLoginTokenOut,
    AdminOut,
    MfaChallengeOut,
)
from app.modules.orchestrator.schemas import NON_TERMINAL_STATUSES

logger = get_logger(__name__)

LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300.0
# 纯 IP 桶(只计失败):换用户名不换桶,兜住遍历账号的口令喷洒;阈值放宽以免误伤
# 办公网 NAT 出口共享同一 IP 的多名管理员
LOGIN_IP_MAX_ATTEMPTS = 30
LOGIN_IP_WINDOW_SECONDS = 3600.0
# 纯账号桶:撞库可以换 IP,但换不了目标账号;15 分钟窗成功即清零,日窗只计失败不清零
LOGIN_ACCT_MAX_ATTEMPTS = 10
LOGIN_ACCT_WINDOW_SECONDS = 900.0
LOGIN_ACCT_DAILY_MAX_ATTEMPTS = 30
LOGIN_ACCT_DAILY_WINDOW_SECONDS = 86400.0

# 与 AdminCreateRequest.password 的 min_length 对齐(引导口令不经 schema,需自查)
PASSWORD_MIN_LENGTH = 12

# ---------- TOTP MFA(安全策略 admin_mfa_enabled,默认开,全部管理角色一视同仁) ----------
# 开关只有全员开/全员关两档,不做按角色/按账号 opt-in。开启时登录只签发挑战票,正式 token
# 只经 confirm_totp_setup / verify_mfa_login 签发;关闭时密码校验通过即签发
MFA_SETUP_TICKET_SECONDS = 600  # 绑定票 10 分钟,一次性用途(typ=mfa_setup)
MFA_VERIFY_TICKET_SECONDS = 300  # 二要素票 5 分钟
MFA_MAX_ATTEMPTS = 5  # 同账号 5 次/10min,防在线爆破 6 位码
MFA_WINDOW_SECONDS = 600.0
RECOVERY_CODE_COUNT = 10

# 单笔调账绝对值上限:超出走对公/线下流程,不进双人复核(防手滑多敲零)
ADJUST_MAX_ABS = Decimal("100000.00")


def _check_password_bytes(password: str) -> None:
    """哈希前按字节数拦截超长口令,否则 bcrypt 5.x 在哈希层抛 ValueError 变 500。"""
    try:
        check_password_bytes(password)
    except ValueError:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="common.validation") from None


def _login_buckets(client_ip: str | None, username: str) -> list[tuple[str, int, float, bool]]:
    """四层登录桶 (键, 上限, 窗口秒, 成功即清零),全部只计失败。IP 桶与日桶不清零:
    口令喷洒不会产生成功登录,清零只会给持续撞库者续命。每次调用重读阈值常量(测试可 monkeypatch)。"""
    ip = client_ip or "-"
    return [
        (f"admin-login-ip:{ip}", LOGIN_IP_MAX_ATTEMPTS, LOGIN_IP_WINDOW_SECONDS, False),
        (f"admin-login:{ip}:{username}", LOGIN_MAX_ATTEMPTS, LOGIN_WINDOW_SECONDS, True),
        (f"admin-login-acct:{username}", LOGIN_ACCT_MAX_ATTEMPTS, LOGIN_ACCT_WINDOW_SECONDS, True),
        (
            f"admin-login-acct-daily:{username}",
            LOGIN_ACCT_DAILY_MAX_ATTEMPTS,
            LOGIN_ACCT_DAILY_WINDOW_SECONDS,
            False,
        ),
    ]


async def login(
    session: AsyncSession, username: str, password: str, *, client_ip: str | None = None
) -> tuple[MfaChallengeOut | AdminLoginTokenOut, AdminUser]:
    """密码校验 → (响应, 账号)。安全策略 admin_mfa_enabled 开启:未绑定 TOTP 发绑定票、
    已绑定发验证票,不直发 token;关闭:直接签发 access token(status=ok)。"""
    admin = (
        await session.execute(select(AdminUser).where(AdminUser.username == username))
    ).scalar_one_or_none()
    buckets = _login_buckets(client_ip, username)
    # 已封禁的桶在 bcrypt(~200ms CPU/次)之前拦下:封禁期内的撞库请求不付哈希成本
    for key, max_attempts, window, _ in buckets:
        await ensure_not_rate_limited(key, max_attempts=max_attempts, window_seconds=window)
    password_ok = await verify_password(
        password, admin.password_hash if admin else DUMMY_PASSWORD_HASH
    )
    if admin is None or not password_ok:
        # 只在失败后计数,四层同计:换 IP 逃不掉账号桶,换账号逃不掉 IP 桶
        for key, max_attempts, window, _ in buckets:
            await check_rate_limit(key, max_attempts=max_attempts, window_seconds=window)
        logger.warning("admin_login_failed", username=username, ip=client_ip)
        raise AppError(ErrorCode.LOGIN_FAILED, key="adminapi.loginFailed")
    if admin.status != "active":
        raise AppError(
            ErrorCode.USER_FROZEN,
            key="adminapi.userDisabled",
            http_status=status.HTTP_403_FORBIDDEN,
        )
    # 凭据正确即清零「成功即清零」的桶(IP 桶与日桶不清)
    for key, _, _, clear_on_success in buckets:
        if clear_on_success:
            await clear_rate_limit(key)
    # 安全策略关闭两步验证:密码即登录(已绑定者也不挑战;重新开启即恢复二要素)
    cfg = await get_effective_platform_config(session)
    if cfg["admin_mfa_enabled"] != "true":
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


# 静默续期:access 过期后 15 分钟宽限内可换发(401 反应式续期的窗口);
# 自首次登录(sess_iat 跨续期链传递)起 12 小时绝对会话上限,到点必须重新登录
RENEW_GRACE_SECONDS = 15 * 60
SESSION_MAX_SECONDS = 12 * 3600


async def renew_access_token(session: AsyncSession, token: str) -> str:
    """有效或刚过期(宽限内)的管理端 access token 换发新 token。
    账号停用/改密/重置(token_version 变)或超绝对会话上限即 401。"""
    from datetime import UTC, datetime, timedelta

    payload = decode_token(token, "admin", leeway_seconds=RENEW_GRACE_SECONDS)
    # 续期链上 iat 每轮刷新,绝对上限须锚定首次登录时刻(sess_iat)
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
    """校验短票并加载账号。票据无效/账号状态或版本已变 → MFA_TICKET_INVALID(重新登录)。"""
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
    """消耗一次 MFA 窗口配额。成功也计:只记失败时,窗口内截获一枚码可无限重放领 token。"""
    await check_rate_limit(
        f"admin-mfa:{admin_id}", max_attempts=MFA_MAX_ATTEMPTS, window_seconds=MFA_WINDOW_SECONDS
    )


def _decrypt_totp_secret(admin: AdminUser) -> str:
    from app.core.crypto import decrypt_str

    assert admin.totp_secret is not None  # 调用方保证(totp_enabled 或 setup 已开始)
    return decrypt_str(admin.totp_secret, aad=f"totp:{admin.id}")


def _match_totp_timestep(secret: str, code: str, *, window: int = 1) -> int | None:
    """手动窗口匹配:返回匹配的 timestep(30s 步长),不匹配返回 None。
    替代 pyotp verify(valid_window=1):防重放需要知道匹配的具体步,据此拒绝已用步。"""
    import time

    import pyotp

    totp = pyotp.TOTP(secret)
    now_step = int(time.time() // 30)
    for offset in range(-window, window + 1):
        step = now_step + offset
        if totp.at(step * 30) == code:
            return step
    return None


def _accept_totp_step(locked: AdminUser, matched_step: int) -> bool:
    """防重放闸(RFC 6238 §5.2,行锁内调用):matched_step 必须大于已通过的最大步,
    通过则单调推进;同一动态码在窗口内重放第二次即被拒。"""
    last = locked.last_totp_timestep
    if last is not None and matched_step <= last:
        return False
    locked.last_totp_timestep = matched_step
    return True


def _gen_plain_recovery_codes() -> list[str]:
    """10 个 XXXXX-XXXXX 恢复码(40 bit/个)。明文只存在于响应当次,落库只有 bcrypt 哈希。"""
    import secrets

    return [f"{(raw := secrets.token_hex(5))[:5]}-{raw[5:]}" for _ in range(RECOVERY_CODE_COUNT)]


async def _hash_recovery_codes(plain: list[str]) -> list[str]:
    """bcrypt ~200ms/个:gather 并发受 _bcrypt_permits(4)约束,10 个约 600ms。"""
    import asyncio

    return list(await asyncio.gather(*(hash_password(code) for code in plain)))


async def _consume_recovery_code(admin: AdminUser, code: str) -> bool:
    """匹配即作废(用后失效)。bcrypt 逐个比对,≤10 个,并发受信号量约束。"""
    import asyncio

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
    复用让绑定页刷新/重进看到同一二维码;确认绑定前 totp_enabled 恒为 false。

    行锁下读改写:并发 begin(双发/双击/多标签页)各读到 totp_secret is None 时会各生成
    一枚密钥、后写者覆盖前者,页面渲染的二维码可能已失效,首个动态码必然验不过。
    """
    import pyotp

    from app.core.crypto import encrypt_str

    admin = await _admin_from_ticket(session, ticket, expected="mfa_setup")
    # populate_existing 不可省:_admin_from_ticket 已把该行读进 identity map,
    # 不强制重读则 get() 直接返回缓存实例,锁拿到了却看的是加锁前的旧值
    locked = await session.get(AdminUser, admin.id, with_for_update=True, populate_existing=True)
    assert locked is not None  # 同一事务刚按票据加载过该行;AdminUser 无删除路径
    if locked.totp_secret is None:
        secret = pyotp.random_base32()
        locked.totp_secret = encrypt_str(secret, aad=f"totp:{locked.id}")
    else:
        secret = _decrypt_totp_secret(locked)
    await session.commit()  # 锁随事务结束释放;未改也要提交,不能把锁留到请求结束
    uri = pyotp.TOTP(secret).provisioning_uri(name=locked.username, issuer_name="SuperDL 管理端")
    return secret, uri


async def confirm_totp_setup(
    session: AsyncSession, ticket: str, code: str
) -> tuple[str, AdminUser, list[str]]:
    """校验首个动态码 → 启用 + 发恢复码(明文仅本次) → 签发正式 token。"""
    admin = await _admin_from_ticket(session, ticket, expected="mfa_setup")
    await _check_mfa_rate(admin.id)
    if admin.totp_secret is None:  # 未 begin 直接 confirm
        raise AppError(ErrorCode.MFA_TICKET_INVALID, key="adminapi.mfaTicketInvalid")
    # 行锁内匹配+防重放推进:绑定阶段重放同一首码会重复签发 token 并重置恢复码
    locked = await session.get(AdminUser, admin.id, with_for_update=True, populate_existing=True)
    assert locked is not None  # 同上:行锁重读只为拿新值,不会读空
    matched = _match_totp_timestep(_decrypt_totp_secret(locked), code)
    if matched is None or not _accept_totp_step(locked, matched):
        await _count_mfa_attempt(admin.id)
        logger.warning("mfa_bind_failed", admin_id=admin.id)
        raise AppError(ErrorCode.MFA_CODE_INVALID, key="adminapi.mfaCodeInvalid")
    await _count_mfa_attempt(admin.id)  # 成功也计配额:窗口内批量领 token 的兜底
    plain = _gen_plain_recovery_codes()
    locked.totp_recovery = await _hash_recovery_codes(plain)
    locked.totp_enabled = True
    # 绑定即告警(检测闭环):绑定只靠口令是结构性事实(平台无管理员带外通道),
    # 抢先绑定窗口(首登/重置后)内被盗口令可静默换绑——告警流是唯一及时发现面
    from app.modules.notify import service as notify_service

    await notify_service.notify(
        session,
        None,  # 平台告警流(管理端告警页)
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
    # 行锁内验证:timestep 推进/恢复码作废必须与「是否已用」的判定原子化,
    # 否则并发重放同一码双双通过(RFC 6238 §5.2 要求同一步只接受一次)
    locked = await session.get(AdminUser, admin.id, with_for_update=True, populate_existing=True)
    assert locked is not None  # 同上:行锁重读只为拿新值,不会读空
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
    await _count_mfa_attempt(admin.id)  # 成功也计配额:窗口内批量领 token 的兜底
    await session.commit()  # timestep 推进 / 恢复码作废落库
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
    """超管为他人重置 TOTP(锁死救援):清空绑定与恢复码并踢掉全部会话,
    下次登录重新走强制绑定。本人不可自重置(恢复码或另一位超管)。"""
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
    logger.warning("mfa_reset", actor_id=actor.id, target_id=target_id)
    return target


async def create_admin(session: AsyncSession, username: str, password: str, role: str) -> AdminUser:
    from sqlalchemy.exc import IntegrityError

    _check_password_bytes(password)
    admin = AdminUser(username=username, password_hash=await hash_password(password), role=role)
    session.add(admin)
    try:
        await session.commit()
    except IntegrityError as exc:  # username 唯一
        await session.rollback()
        raise conflict(key="adminapi.adminUsernameTaken") from exc
    await session.refresh(admin)
    return admin


async def ensure_bootstrap_admin(session: AsyncSession, password: str) -> None:
    """首个管理员引导(scripts/seed_dev.py 调用):**表为空时**创建 admin 账号,之后自动失效。"""
    existing = (await session.execute(select(AdminUser).limit(1))).scalar_one_or_none()
    if existing is not None:
        return
    # 引导口令不经请求 schema,长度须与 AdminCreateRequest 同标准;不合规宁可报错退出
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
        # 角色变更/停用立即失效已签发的 token
        admin.token_version += 1
    await session.commit()
    await session.refresh(admin)
    return admin, before


async def reset_admin_password(session: AsyncSession, admin_id: int, password: str) -> AdminUser:
    admin = await _get_admin(session, admin_id)
    _check_password_bytes(password)
    admin.password_hash = await hash_password(password)
    admin.token_version += 1  # 改密即踢掉全部在外会话
    await session.commit()
    await session.refresh(admin)
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
    """服务端登出:token_version+1(行锁内),已签发的 access token 即刻全失效。
    前端只清本地态的登出把被盗 token 留到自然过期;吊销语义必须在服务端。"""
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
    幂等键作用域为 (发起人,租户,键);同键重放比对请求体指纹,不一致 409
    (对齐 Stripe 惯例):弱键跨租户/跨金额复用得到显式拒绝而非静默错单。"""
    from app.core.money import as_amount
    from app.modules.account import service as account_service

    amount = as_amount(Decimal(str(amount)))
    fingerprint = request_fingerprint(user_id, amount, reason)

    if idempotency_key:
        # 归属是 (created_by, user_id) 双列,find_replay 只支持单列:重放查询保持手写
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
            return existing, False  # 幂等重放:返回已受理的调账单,不重复开单

    # 用户必须存在:否则复核通过时 wallet 会为幽灵 user_id 凭空建钱包并入账
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
    # 并发同键撞 uq_admin_adjustments_idem_scope 由唯一约束兜底(500),不回查:
    # 与其它 check-then-insert 路径同一立场,重试即命中上面的重放分支
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
    audit_writer:同步审计钩子,approve 分支最终 commit 前调用,写失败即整体回滚。"""
    from app.modules.billing import service as billing_service

    # 行锁:并发复核时后到者等锁,看到非 pending 即 409
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
        # 防自建第二账号绕复核:发起后才创建的账号不构成独立的第二人
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
            allow_negative=True,  # 冲正金额不受当前余额封顶
        )
    if audit_writer is not None:
        await audit_writer(session)  # 同步审计:与生效同事务,写失败即回滚
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
    """核销渠道冲正(异常清单 channel_reversed 分桶的唯一出口)。

    - release:核实为渠道噪音/误通知——解冻等额冻结额,清标记(订单恢复退款资格);
    - chargeback:确认钱已被渠道拿回——解冻 + 等额扣减(ledger adjust,允许透支;
      订单标记保留,永不恢复退款资格)。
    单操作人 + 同步审计:与调账的双人复核不同,这里不新增资金敞口(只回收或解冻),
    风险方向是「少收」,由审计行与异常清单闭环追溯。
    """
    from app.modules.billing import service as billing_service

    order = (
        await session.execute(
            select(billing_service.Order)
            .where(billing_service.Order.order_no == order_no)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if order is None:
        raise not_found("订单不存在")
    if order.channel_reversed_at is None:
        raise conflict(key="adminapi.reversalNotPending")
    await billing_service.release_freeze(session, order.user_id, order.amount)
    if action == "release":
        order.channel_reversed_at = None
    else:
        await billing_service.debit(
            session,
            order.user_id,
            order.amount,
            type_="adjust",
            ref_type="reversal",
            ref_id=order.order_no,
            remark=f"渠道冲正核销:{reason}",
            allow_negative=True,  # 用户可能已花掉:核销后余额为负属预期,走欠费链路
        )
    if audit_writer is not None:
        await audit_writer(session)  # 同步审计:与核销同事务,写失败即回滚
    await session.commit()
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
    from app.core.pagination import paginate_by_id

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


# ---------- 只读聚合(总览/调账上下文/改价影响面;均不碰写路径) ----------


async def overview(session: AsyncSession) -> dict[str, Any]:
    """运营总览聚合:精确 COUNT 口径,不从截断列表推算。

    组成受模块边界约束(只许调对方 service):
    - 实例分状态计数:list_instances_by_status 逐状态装载计数;released 终态不统计
      (历史行无界)。
    - 付费租户:ledger consume 全表聚合精确计数;租户总数取 active 用户口径。
    - 包周期在保数:未到期的订阅行数(不是实例状态数)—— 停机的包月实例仍在保,
      按实例状态数会把它漏掉,而它恰恰还占着库存。
    - 节点/GPU:台账全量(含 NotReady/Missing,前端据此画非 Ready 段);已租那段再拆出
      竞价占用(可回收容量),按台账的 gpu_used 截断(口径见 orchestrator 侧的查询)。
    """
    from app.modules.account import service as account_service
    from app.modules.billing import service as billing_service
    from app.modules.nodes import service as nodes_service
    from app.modules.orchestrator import service as orchestrator_service

    status_counts: dict[str, int] = {}
    for st in NON_TERMINAL_STATUSES:
        status_counts[st] = len(await orchestrator_service.list_instances_by_status(session, st))

    consumed = await billing_service.consumed_by_user(session)
    active_user_ids = await account_service.list_active_user_ids(session)

    pools: dict[str, dict[str, int]] = {}
    nodes_ready = nodes_missing = 0
    specs = await nodes_service.list_node_specs(session)
    spot_by_pool = await orchestrator_service.running_spot_gpus_by_pool(session)
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
    """调账前置上下文(只读):确认租户身份 + 当前余额 + 近 3 条流水。用户不存在 → 404。"""
    from app.modules.account import service as account_service
    from app.modules.billing import service as billing_service
    from app.modules.orchestrator import service as orchestrator_service

    user = await account_service.get_user(session, user_id)
    balance = await billing_service.get_balance(session, user_id)
    recent = await billing_service.ledger_page(session, user_id, limit=3)
    running_by_user = await orchestrator_service.list_running_instances_by_user(session)
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
    from app.modules.orchestrator import service as orchestrator_service

    active = []
    for st in ("creating", "starting", "running"):
        active.extend(await orchestrator_service.list_instances_by_status(session, st))
    mine = [i for i in active if i.sku_id == sku_id]
    return {
        "sku_id": sku_id,
        "active_instances": len(mine),
        "active_users": len({i.user_id for i in mine}),
        "active_gpus": sum(i.gpu_count for i in mine),
    }
