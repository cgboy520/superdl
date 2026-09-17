import math
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import status
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.captcha import CaptchaError, get_captcha_channel
from app.core.config import get_settings
from app.core.crypto import hash_id_number_candidates, hash_sms_code, hash_sms_code_candidates
from app.core.errors import AppError, ErrorCode, conflict, not_found, unauthorized
from app.core.logging import get_logger, mask_phone_value
from app.core.loginguard import LoginBucket, login_attempt, login_failed, login_succeeded
from app.core.metrics import LOGIN_FAILED_TOTAL, USER_SIGNUP_TOTAL, VERIFICATION_SENT_TOTAL
from app.core.pagination import RawPage, clamp_limit, decode_cursor_int, slice_page
from app.core.platform_config import get_runtime_config
from app.core.ratelimit import check_rate_limit, clear_rate_limit, read_hits
from app.core.security import (
    create_token,
    decode_token,
    dummy_password_hash,
    hash_password,
    verify_password,
)
from app.core.sms import SmsError, ensure_sms_platform_quota, get_sms_channel
from app.core.sqlutil import like_escape
from app.core.timeutil import ensure_utc, local_day_range, now_utc
from app.modules.account.models import (
    SmsCode,
    SshKey,
    UsedRefreshToken,
    User,
    UserQuotaOverride,
)
from app.modules.account.realname import (
    RealNameError,
    get_realname_provider,
    mask_id_name,
    mask_id_number,
)
from app.modules.account.schemas import TokenPair, UserOut
from app.modules.legal import service as legal_service

logger = get_logger(__name__)

MOCK_SMS_CODE = "123456"

MAX_SMS_CODE_ATTEMPTS = 5

SMS_SEND_BACKOFF_MAX_EXPONENT = 3

SMS_CONSUME_DAILY_MAX = 10

SMS_SEND_IP_HOURLY_MAX = 20

SMS_SEND_PHONE_DAILY_MAX = 15

SMS_PRECHECK_PHONE_HOURLY_MAX = 30

REFRESH_REPLAY_GRACE_SECONDS = 10.0


async def _verify_captcha(session: AsyncSession, captcha_token: str | None) -> None:
    """captcha_enabled 时校验人机 token:缺失 400、渠道故障 502、不通过 400。"""
    if not captcha_token:
        raise AppError(ErrorCode.CAPTCHA_REQUIRED, key="account.captchaRequired")
    try:
        channel = await get_captcha_channel(session)
        captcha_ok = await channel.verify(captcha_token)
    except CaptchaError as exc:
        logger.error("captcha_channel_error", error=str(exc))
        raise AppError(
            ErrorCode.CAPTCHA_CHANNEL_ERROR,
            key="account.captchaChannelError",
            http_status=status.HTTP_502_BAD_GATEWAY,
        ) from exc
    if not captcha_ok:
        raise AppError(ErrorCode.CAPTCHA_VERIFY_FAILED, key="account.captchaVerifyFailed")


async def _enforce_send_backoff(session: AsyncSession, phone: str) -> None:
    """同号最新一条距今不足基础间隔即拒;连续 N 条未成功消费(含失败作废 / 过期)时
    间隔 = 基础 × 2^(N-1),指数封顶 SMS_SEND_BACKOFF_MAX_EXPONENT。调用方须持手机号咨询锁。"""
    recent = list(
        (
            await session.execute(
                select(SmsCode)
                .where(SmsCode.phone == phone, SmsCode.created_at > now_utc() - timedelta(hours=24))
                .order_by(SmsCode.id.desc())
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
    required = base * (2 ** min(max(streak, 1) - 1, SMS_SEND_BACKOFF_MAX_EXPONENT))
    elapsed = (now_utc() - ensure_utc(recent[0].created_at)).total_seconds()
    if elapsed < required:
        raise AppError(
            ErrorCode.SMS_TOO_FREQUENT,
            key="account.smsTooFrequent",
            params={"seconds": math.ceil(required - elapsed)},
            http_status=status.HTTP_429_TOO_MANY_REQUESTS,
        )


async def send_sms_code(
    session: AsyncSession,
    phone: str,
    purpose: str,
    *,
    client_ip: str | None = None,
    captcha_token: str | None = None,
) -> None:
    """按手机号加事务咨询锁创建验证码;提交后发送,渠道失败时作废验证码。
    闸门顺序:按号预检桶 → 人机验证 → 按 IP 桶 → 退避 → 按号日发送上限 → 平台 verify 预算。"""
    settings = get_settings()
    await check_rate_limit(
        f"sms-precheck-phone:{phone}",
        max_attempts=SMS_PRECHECK_PHONE_HOURLY_MAX,
        window_seconds=3600.0,
    )
    cfg = await get_runtime_config(session)
    if cfg.captcha_enabled:
        await _verify_captcha(session, captcha_token)
    await check_rate_limit(
        f"sms-send-ip:{client_ip or '-'}",
        max_attempts=SMS_SEND_IP_HOURLY_MAX,
        window_seconds=3600.0,
    )
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:phone))"), {"phone": phone})
    await _enforce_send_backoff(session, phone)
    await check_rate_limit(
        f"sms-send-phone:{phone}", max_attempts=SMS_SEND_PHONE_DAILY_MAX, window_seconds=86400.0
    )
    await ensure_sms_platform_quota("verify")
    code = MOCK_SMS_CODE if cfg.sms_provider == "mock" else f"{secrets.randbelow(10**6):06d}"
    row = SmsCode(
        phone=phone,
        code_hash=hash_sms_code(phone, purpose, code),
        purpose=purpose,
        expires_at=now_utc() + timedelta(seconds=settings.sms_code_ttl_seconds),
    )
    session.add(row)
    await session.commit()
    try:
        channel = await get_sms_channel(session)
        await channel.send(phone, "verify", {"code": code})
        VERIFICATION_SENT_TOTAL.labels(channel="sms", purpose=purpose).inc()
    except SmsError as exc:
        row.used_at = now_utc()
        await session.commit()
        logger.error("sms_send_failed", phone=phone, error=str(exc))
        raise AppError(
            ErrorCode.SMS_SEND_FAILED,
            key="account.smsSendFailed",
            http_status=status.HTTP_502_BAD_GATEWAY,
        ) from exc


async def _consume_sms_code(session: AsyncSession, phone: str, code: str, purpose: str) -> None:
    """行锁下校验并消费验证码;成功受日配额限制,不提交。

    验码失败提交失败计次后抛 SMS_CODE_INVALID,达到 MAX_SMS_CODE_ATTEMPTS 时作废。
    """
    row = (
        await session.execute(
            select(SmsCode)
            .where(
                SmsCode.phone == phone,
                SmsCode.purpose == purpose,
                SmsCode.used_at.is_(None),
                SmsCode.expires_at > now_utc(),
            )
            .order_by(SmsCode.id.desc())
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise AppError(ErrorCode.SMS_CODE_INVALID, key="account.smsCodeInvalid")
    matched = [
        secrets.compare_digest(row.code_hash, c)
        for c in hash_sms_code_candidates(phone, purpose, code)
    ]
    if not any(matched):
        row.attempts += 1
        if row.attempts >= MAX_SMS_CODE_ATTEMPTS:
            row.used_at = now_utc()
        await session.commit()
        raise AppError(ErrorCode.SMS_CODE_INVALID, key="account.smsCodeInvalid")
    await check_rate_limit(
        f"sms-consume-phone:{phone}", max_attempts=SMS_CONSUME_DAILY_MAX, window_seconds=86400.0
    )
    row.used_at = row.consumed_at = now_utc()


def _issue_tokens(
    user: User,
    *,
    refresh_jti: str | None = None,
    access_jti: str | None = None,
    iat: datetime | None = None,
) -> TokenPair:
    """签发 token 对;指定 jti/iat 可重建刷新宽限窗内的轮换结果。"""
    extra = {"ver": user.token_version}
    return TokenPair(
        access_token=create_token(
            str(user.id), "user", token_type="access", extra=extra, jti=access_jti, iat=iat
        ),
        refresh_token=create_token(
            str(user.id), "user", token_type="refresh", extra=extra, jti=refresh_jti, iat=iat
        ),
        user=UserOut.model_validate(user),
    )


async def register(
    session: AsyncSession,
    phone: str,
    sms_code: str,
    password: str | None,
    *,
    accept_terms: bool = False,
    client_ip: str | None = None,
) -> TokenPair:
    if not accept_terms:
        raise AppError(ErrorCode.TERMS_NOT_ACCEPTED, key="account.termsNotAccepted")
    await check_rate_limit(
        f"user-register:{client_ip or '-'}:{phone}", max_attempts=5, window_seconds=300.0
    )
    await _consume_sms_code(session, phone, sms_code, "register")
    existing = (await session.execute(select(User).where(User.phone == phone))).scalar_one_or_none()
    if existing is not None:
        logger.warning(
            "user_register_failed",
            account=mask_phone_value(phone),
            ip=client_ip,
            reason="phone_taken",
        )
        raise AppError(ErrorCode.PHONE_TAKEN, key="account.phoneTaken")
    user = User(phone=phone, password_hash=await hash_password(password) if password else None)
    session.add(user)
    await session.flush()
    await legal_service.record_registration_consents(session, user.id, client_ip)
    await session.commit()
    await session.refresh(user)
    USER_SIGNUP_TOTAL.inc()
    logger.info("user_registered", user_id=user.id)
    return _issue_tokens(user)


LOGIN_IP_MAX, LOGIN_IP_WINDOW = 60, 3600.0
LOGIN_PAIR_MAX, LOGIN_PAIR_WINDOW = 5, 300.0
LOGIN_ACCT_MAX, LOGIN_ACCT_WINDOW = 10, 900.0
LOGIN_ACCT_DAILY_MAX, LOGIN_ACCT_DAILY_WINDOW = 30, 86400.0


def _acct_bucket_key(phone: str) -> str:
    return f"user-login-acct:{phone}"


def _login_buckets(phone: str, client_ip: str | None) -> list[LoginBucket]:
    ip = client_ip or "-"
    return [
        LoginBucket(f"user-login-ip:{ip}", LOGIN_IP_MAX, LOGIN_IP_WINDOW),
        LoginBucket(
            f"user-login:{ip}:{phone}", LOGIN_PAIR_MAX, LOGIN_PAIR_WINDOW, clear_on_success=True
        ),
        LoginBucket(_acct_bucket_key(phone), LOGIN_ACCT_MAX, LOGIN_ACCT_WINDOW),
        LoginBucket(
            f"user-login-acct-daily:{phone}", LOGIN_ACCT_DAILY_MAX, LOGIN_ACCT_DAILY_WINDOW
        ),
    ]


class _LoginFailed(AppError):
    """凭据错误(「未注册」与「凭证错」对外不可区分);registered 只进日志。"""

    def __init__(self, *, registered: bool) -> None:
        super().__init__(ErrorCode.LOGIN_FAILED, key="account.loginFailed")
        self.registered = registered


async def _verify_credentials(
    session: AsyncSession, phone: str, *, sms_code: str | None, password: str | None
) -> User:
    """验证码优先于密码;凭据无效或账号不存在时抛 _LoginFailed。"""
    user = (await session.execute(select(User).where(User.phone == phone))).scalar_one_or_none()
    if sms_code is not None:
        try:
            await _consume_sms_code(session, phone, sms_code, "login")
        except AppError as exc:
            raise _LoginFailed(registered=user is not None) from exc
        if user is None:
            raise _LoginFailed(registered=False)
        await session.commit()
        return user
    if password is None:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="account.credentialRequired")
    await session.commit()
    stored = (
        user.password_hash if (user is not None and user.password_hash) else dummy_password_hash()
    )
    password_ok = await verify_password(password, stored)
    if user is None or user.password_hash is None or not password_ok:
        raise _LoginFailed(registered=user is not None)
    return user


async def _notify_login_anomaly(session: AsyncSession, user: User) -> None:
    """成功登录后通知账号 15 分钟窗内的失败记录,再清零该桶。"""
    key = _acct_bucket_key(user.phone)
    acct_hits = await read_hits(key, window_seconds=LOGIN_ACCT_WINDOW)
    if acct_hits > 0:
        from app.modules.notify import service as notify_service  # noqa: PLC0415

        await notify_service.notify(
            session,
            user.id,
            type_="account",
            title="检测到异常登录尝试",
            content=(
                f"您的账号近 15 分钟内有 {acct_hits} 次登录失败记录,本次登录成功。"
                "若非本人操作,请立即修改密码并检查账号安全。"
            ),
            severity="warning",
            dedup_key=f"login-anomaly:{user.id}:{now_utc():%Y%m%d}",
        )
        await session.commit()
    await clear_rate_limit(key)


async def login(
    session: AsyncSession,
    phone: str,
    sms_code: str | None,
    password: str | None,
    *,
    client_ip: str | None = None,
) -> TokenPair:
    """密码或验证码登录。密码路径 bcrypt 前四层桶先计数再判定;失败留痕;
    成功退还预计数、清零配对桶并判异常登录。验证码路径只在失败后计数。"""
    buckets = _login_buckets(phone, client_ip)
    precounted = password is not None
    if precounted:
        await login_attempt(buckets)
    try:
        user = await _verify_credentials(session, phone, sms_code=sms_code, password=password)
    except _LoginFailed as exc:
        await login_failed(buckets, precounted=precounted)
        LOGIN_FAILED_TOTAL.labels(actor_type="user").inc()
        logger.warning(
            "user_login_failed",
            account=mask_phone_value(phone),
            ip=client_ip,
            via="sms" if sms_code is not None else "password",
            registered=exc.registered,
        )
        raise
    await login_succeeded(buckets, precounted=precounted)
    if password is not None:
        await _notify_login_anomaly(session, user)
    if user.status == "frozen":
        raise AppError(
            ErrorCode.USER_FROZEN, key="account.userFrozen", http_status=status.HTTP_403_FORBIDDEN
        )
    return _issue_tokens(user)


async def reset_password(
    session: AsyncSession,
    phone: str,
    sms_code: str,
    new_password: str,
    *,
    client_ip: str | None = None,
) -> TokenPair:
    """凭手机号 + 验证码设置新密码(首次设置、修改、找回同一条路径)。

    先验码再锁定账号;成功后 token_version+1 撤销全部在外会话并发新 token 对。
    """
    await check_rate_limit(
        f"password-reset:{client_ip or '-'}:{phone}", max_attempts=5, window_seconds=300.0
    )
    await _consume_sms_code(session, phone, sms_code, "reset_password")
    user = (
        await session.execute(select(User).where(User.phone == phone).with_for_update())
    ).scalar_one_or_none()
    if user is None:
        logger.warning(
            "password_reset_failed",
            account=mask_phone_value(phone),
            ip=client_ip,
            reason="no_such_user",
        )
        raise AppError(ErrorCode.LOGIN_FAILED, key="account.loginFailed")
    if user.status == "frozen":
        raise AppError(
            ErrorCode.USER_FROZEN, key="account.userFrozen", http_status=status.HTTP_403_FORBIDDEN
        )
    user.password_hash = await hash_password(new_password)
    user.token_version += 1
    await session.commit()
    await session.refresh(user)
    logger.info("password_reset", user_id=user.id)
    return _issue_tokens(user)


async def refresh_tokens(session: AsyncSession, refresh_token: str) -> TokenPair:
    """持用户行锁轮换 refresh;版本缺失或不匹配时拒绝。

    宽限窗内重放刷新返回登记的 token 对,缺失登记时补发;登出重放拒绝。
    其余重放递增 token_version 并提交,撤销全部会话。
    """
    payload = decode_token(refresh_token, "user", expected_type="refresh")
    user = await session.get(User, int(payload["sub"]), with_for_update=True)
    if user is None or user.status == "frozen":
        raise unauthorized()
    if user.status == "deleted":
        raise unauthorized(key="account.accountDeleted")
    if payload.get("ver") != user.token_version:
        raise unauthorized()
    jti = str(payload.get("jti", ""))
    inserted = (
        await session.execute(
            pg_insert(UsedRefreshToken)
            .values(
                jti=jti,
                expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
                consumed_via="refresh",
            )
            .on_conflict_do_nothing(index_elements=["jti"])
            .returning(UsedRefreshToken.jti)
        )
    ).scalar_one_or_none()
    if inserted is None:
        used = await session.get(UsedRefreshToken, jti)
        if used is not None and used.used_at > now_utc() - timedelta(
            seconds=REFRESH_REPLAY_GRACE_SECONDS
        ):
            if used.consumed_via == "logout":
                logger.warning("logout_consumed_token_replayed", user_id=user.id)
                raise unauthorized()
            if used.consumed_via == "refresh":
                if used.replaced_refresh_jti is not None and used.replaced_iat is not None:
                    return _issue_tokens(
                        user,
                        refresh_jti=used.replaced_refresh_jti,
                        access_jti=used.replaced_access_jti,
                        iat=ensure_utc(used.replaced_iat),
                    )
                return _issue_tokens(user)
        user.token_version += 1
        await session.commit()
        logger.warning("refresh_token_replayed", user_id=user.id)
        raise unauthorized()
    new_refresh_jti = secrets.token_hex(16)
    new_access_jti = secrets.token_hex(16)
    issued_at = now_utc()
    consumed = await session.get(UsedRefreshToken, jti)
    assert consumed is not None
    consumed.replaced_refresh_jti = new_refresh_jti
    consumed.replaced_access_jti = new_access_jti
    consumed.replaced_iat = issued_at
    await session.commit()
    return _issue_tokens(
        user, refresh_jti=new_refresh_jti, access_jti=new_access_jti, iat=issued_at
    )


async def logout(session: AsyncSession, refresh_token: str) -> None:
    """登记 refresh 为已登出并提交;无效、过期或已登出时静默返回。

    已签发 access 的即时撤销由 logout_all 完成。
    """
    try:
        payload = decode_token(refresh_token, "user", expected_type="refresh")
    except AppError:
        return
    user_id = int(payload["sub"])
    if await session.get(User, user_id) is None:
        return
    await session.execute(
        pg_insert(UsedRefreshToken)
        .values(
            jti=str(payload.get("jti", "")),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
            consumed_via="logout",
        )
        .on_conflict_do_nothing(index_elements=["jti"])
    )
    await session.commit()


async def logout_all(session: AsyncSession, user_id: int) -> None:
    """持用户行锁递增 token_version 并提交,撤销全部 access/refresh。"""
    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        raise not_found()
    user.token_version += 1
    await session.commit()


async def get_user(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise not_found()
    return user


async def submit_real_name(session: AsyncSession, user: User, name: str, id_number: str) -> User:
    """实名认证:三要素核验,通过即 verified。身份证号只存脱敏串,原文不落库不打日志。"""
    if user.verification_status == "verified":
        raise conflict(key="account.realNameDone")
    cfg = await get_runtime_config(session)
    if not cfg.real_name_enabled:
        raise AppError(
            ErrorCode.REAL_NAME_DISABLED,
            key="account.realNameDisabled",
            http_status=status.HTTP_409_CONFLICT,
        )
    await check_rate_limit(f"real-name:{user.id}", max_attempts=5, window_seconds=3600.0)
    try:
        provider = await get_realname_provider(session)
        ok = await provider.verify(name, id_number, user.phone)
    except RealNameError as exc:
        logger.error("real_name_channel_error", user_id=user.id, error=str(exc))
        raise AppError(
            ErrorCode.REAL_NAME_CHANNEL_ERROR,
            key="account.realNameChannelError",
            http_status=status.HTTP_502_BAD_GATEWAY,
        ) from exc
    if not ok:
        raise AppError(ErrorCode.REAL_NAME_MISMATCH, key="account.realNameMismatch")
    digest_candidates = hash_id_number_candidates(id_number)
    bound = (
        await session.execute(
            select(func.count()).where(
                User.id_number_hmac.in_(digest_candidates),
                User.id != user.id,
                User.status != "deleted",
            )
        )
    ).scalar_one()
    max_accounts = get_settings().real_name_max_accounts_per_identity
    if bound >= max_accounts:
        logger.warning("real_name_identity_limit", user_id=user.id, bound=bound)
        raise conflict(key="account.realNameIdentityLimit", params={"max": max_accounts})
    user.id_name = name
    user.id_number = mask_id_number(id_number)
    user.id_number_hmac = digest_candidates[0]
    user.verification_status = "verified"
    await session.commit()
    logger.info("real_name_verified", user_id=user.id)
    return user


async def set_warn_threshold(session: AsyncSession, user: User, hours: int) -> User:
    user.low_balance_warn_hours = hours
    await session.commit()
    return user


async def ssh_keys_by_ids(session: AsyncSession, user_id: int, ids: list[int]) -> list[SshKey]:
    """返回本用户名下给定 id 集合内的公钥,按 id 升序。"""
    if not ids:
        return []
    return list(
        (
            await session.execute(
                select(SshKey)
                .where(SshKey.user_id == user_id, SshKey.id.in_(ids))
                .order_by(SshKey.id)
            )
        ).scalars()
    )


async def is_active_user(session: AsyncSession, user_id: int) -> bool:
    """归属校验:user_id 存在且 active。"""
    status = await session.scalar(select(User.status).where(User.id == user_id))
    return status == "active"


async def require_real_name_if_required(session: AsyncSession, user: User, *, key: str) -> None:
    """real_name_required_for_recharge=true 时拒绝未实名用户(403)。"""
    cfg = await get_runtime_config(session)
    if cfg.real_name_required_for_recharge and user.verification_status != "verified":
        raise AppError(ErrorCode.REAL_NAME_REQUIRED, key=key, http_status=403)


async def get_warn_thresholds(session: AsyncSession, user_ids: list[int]) -> dict[int, int]:
    """返回 user_id 到余额预警阈值小时数的映射。"""
    if not user_ids:
        return {}
    rows = (
        (
            await session.execute(
                select(User.id, User.low_balance_warn_hours).where(User.id.in_(user_ids))
            )
        )
        .tuples()
        .all()
    )
    return dict(rows)


async def list_active_user_ids(session: AsyncSession) -> list[int]:
    """返回全部 active 用户 id。"""
    return list((await session.execute(select(User.id).where(User.status == "active"))).scalars())


async def signup_counts(session: AsyncSession, *, tz_offset_minutes: int = 0) -> dict[str, int]:
    """今日/昨日新注册数(本地日界)。"""
    day_start, _ = local_day_range(tz_offset_minutes)
    prev_day_start = day_start - timedelta(days=1)

    async def _count(start: datetime, end: datetime | None = None) -> int:
        stmt = select(func.count()).select_from(User).where(User.created_at >= start)
        if end is not None:
            stmt = stmt.where(User.created_at < end)
        return (await session.execute(stmt)).scalar_one()

    return {
        "today_signups": await _count(day_start),
        "yesterday_signups": await _count(prev_day_start, day_start),
    }


async def admin_list_users(
    session: AsyncSession,
    *,
    q: str | None = None,
    status: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
    order: str = "desc",
) -> RawPage[User]:
    """按 id 游标分页查询租户;order='asc' 升序,其余降序。

    q 去除首尾空白后,长度至少 11 时精确匹配手机号,否则匹配后缀。
    """
    lim = clamp_limit(limit)
    ascending = order == "asc"
    stmt = select(User).order_by(User.id.asc() if ascending else User.id.desc()).limit(lim + 1)
    if status:
        stmt = stmt.where(User.status == status)
    q = (q or "").strip()
    if q:
        if len(q) >= 11:
            stmt = stmt.where(User.phone == q)
        else:
            stmt = stmt.where(User.phone.like(f"%{like_escape(q)}", escape="\\"))
    last_id = decode_cursor_int(cursor)
    if last_id is not None:
        stmt = stmt.where(User.id > last_id if ascending else User.id < last_id)
    rows = list((await session.execute(stmt)).scalars())
    page_items, next_cursor = slice_page(rows, lim, key=lambda r: r.id)
    return RawPage(items=page_items, next_cursor=next_cursor)


async def frozen_user_ids(session: AsyncSession) -> list[int]:
    """返回全部 frozen 租户 id。"""
    return list((await session.execute(select(User.id).where(User.status == "frozen"))).scalars())


async def admin_set_user_status(session: AsyncSession, user_id: int, status_: str) -> User:
    """只管 users 表。不 commit,调用方把「停机」编排进同一事务。"""
    user = await get_user(session, user_id)
    user.status = status_
    if status_ == "frozen":
        user.token_version += 1
    await session.flush()
    return user


@dataclass(frozen=True)
class UserLimits:
    """每用户配额生效值(实例数 / GPU 总数 / 数据盘块数)。"""

    max_instances: int
    max_gpus: int
    max_disks: int


async def get_user_limits(session: AsyncSession, user_id: int) -> UserLimits:
    """逐项返回用户配额覆盖值,未覆盖项使用平台运行时配置。"""
    policies = await get_runtime_config(session)
    override = await session.get(UserQuotaOverride, user_id)
    return UserLimits(
        max_instances=(
            override.max_instances
            if override is not None and override.max_instances is not None
            else policies.max_instances_per_user
        ),
        max_gpus=(
            override.max_gpus
            if override is not None and override.max_gpus is not None
            else policies.max_gpus_per_user
        ),
        max_disks=(
            override.max_disks
            if override is not None and override.max_disks is not None
            else policies.max_disks_per_user
        ),
    )


async def get_quota_override(session: AsyncSession, user_id: int) -> UserQuotaOverride | None:
    """读覆盖行(无覆盖返回 None)。幽灵 id → 404。"""
    await get_user(session, user_id)
    return await session.get(UserQuotaOverride, user_id)


async def set_quota_override(
    session: AsyncSession,
    user_id: int,
    *,
    max_gpus: int | None,
    max_instances: int | None,
    max_disks: int | None,
    note: str,
    updated_by: int,
) -> UserQuotaOverride | None:
    """写覆盖(upsert);三项全 None = 清除覆盖。不 commit,由调用方与审计同事务提交。"""
    await get_user(session, user_id)
    row = await session.get(UserQuotaOverride, user_id)
    if max_gpus is None and max_instances is None and max_disks is None:
        if row is not None:
            await session.delete(row)
            await session.flush()
        return None
    if row is None:
        row = UserQuotaOverride(user_id=user_id, note=note, updated_by=updated_by)
        session.add(row)
    row.max_gpus = max_gpus
    row.max_instances = max_instances
    row.max_disks = max_disks
    row.note = note
    row.updated_by = updated_by
    await session.flush()
    return row


def realname_view(user: User, *, masked: bool) -> tuple[str, str | None]:
    """返回实名状态与姓名;masked=True 脱敏,False 回明文且调用方须落审计。"""
    if not masked:
        return user.verification_status, user.id_name
    return user.verification_status, mask_id_name(user.id_name) if user.id_name else None
