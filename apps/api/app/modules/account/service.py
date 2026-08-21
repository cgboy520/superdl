import secrets
from datetime import UTC, datetime, timedelta

from fastapi import status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode, not_found, unauthorized
from app.core.logging import get_logger
from app.core.platform_config import get_effective_platform_config
from app.core.ratelimit import check_rate_limit
from app.core.security import create_token, decode_token, hash_password, verify_password
from app.core.sms import SmsError, get_sms_channel
from app.core.timeutil import now_utc
from app.modules.account.models import SmsCode, SshKey, UsedRefreshToken, User
from app.modules.account.schemas import TokenPair, UserOut
from app.modules.account.sshkey_util import parse_public_key

logger = get_logger(__name__)

MOCK_SMS_CODE = "123456"

# 单条验证码最多允许失败次数,达到即作废(防 TTL 窗口内穷举 6 位码)
MAX_SMS_CODE_ATTEMPTS = 5


async def send_sms_code(
    session: AsyncSession, phone: str, purpose: str, *, client_ip: str | None = None
) -> None:
    settings = get_settings()
    # IP 维度限流 + 手机号维度限日发送量(60s 间隔由下方 DB 记录把关)
    await check_rate_limit(
        f"sms-send-ip:{client_ip or '-'}", max_attempts=20, window_seconds=3600.0
    )
    await check_rate_limit(f"sms-send-phone:{phone}", max_attempts=10, window_seconds=86400.0)
    interval = timedelta(seconds=settings.sms_send_interval_seconds)
    recent = (
        await session.execute(
            select(SmsCode)
            .where(SmsCode.phone == phone, SmsCode.created_at > now_utc() - interval)
            .limit(1)
        )
    ).scalar_one_or_none()
    if recent is not None:
        raise AppError(
            ErrorCode.SMS_TOO_FREQUENT,
            key="account.smsTooFrequent",
            params={"seconds": settings.sms_send_interval_seconds},
            http_status=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    cfg = await get_effective_platform_config(session)
    code = MOCK_SMS_CODE if cfg["sms_provider"] == "mock" else f"{secrets.randbelow(10**6):06d}"
    row = SmsCode(
        phone=phone,
        code=code,
        purpose=purpose,
        expires_at=now_utc() + timedelta(seconds=settings.sms_code_ttl_seconds),
    )
    session.add(row)
    await session.commit()
    try:
        channel = await get_sms_channel(session)
        await channel.send(phone, cfg["sms_template_verify"] or "", {"code": code})
    except SmsError as exc:
        # 渠道失败:作废刚落库的验证码
        row.used_at = now_utc()
        await session.commit()
        logger.error("sms_send_failed", phone=phone, error=str(exc))
        raise AppError(
            ErrorCode.SMS_SEND_FAILED,
            key="account.smsSendFailed",
            http_status=status.HTTP_502_BAD_GATEWAY,
        ) from exc


async def _consume_sms_code(session: AsyncSession, phone: str, code: str, purpose: str) -> None:
    """校验并一次性消费验证码。同事务内调用,失败抛 SMS_CODE_INVALID。

    失败计次先 commit 再抛(调用方 rollback 不抹掉计次);
    最新一条达到 MAX_SMS_CODE_ATTEMPTS 即作废,正确码也不再放行。
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
    if row.attempts >= MAX_SMS_CODE_ATTEMPTS or not secrets.compare_digest(row.code, code):
        row.attempts += 1
        await session.commit()
        raise AppError(ErrorCode.SMS_CODE_INVALID, key="account.smsCodeInvalid")
    row.used_at = now_utc()


def _issue_tokens(user: User) -> TokenPair:
    extra = {"ver": user.token_version}
    return TokenPair(
        access_token=create_token(str(user.id), "user", token_type="access", extra=extra),
        refresh_token=create_token(str(user.id), "user", token_type="refresh", extra=extra),
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
    # 先验码再判重:反过来就是手机号枚举 oracle —— 无需持有该号码即可批量探测
    # 「这个号注册过没有」(每个号一个限流桶,换号即换桶)
    await _consume_sms_code(session, phone, sms_code, "register")
    existing = (await session.execute(select(User).where(User.phone == phone))).scalar_one_or_none()
    if existing is not None:
        raise AppError(ErrorCode.PHONE_TAKEN, key="account.phoneTaken")
    user = User(phone=phone, password_hash=hash_password(password) if password else None)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    logger.info("user_registered", user_id=user.id)
    return _issue_tokens(user)


async def login(
    session: AsyncSession,
    phone: str,
    sms_code: str | None,
    password: str | None,
    *,
    client_ip: str | None = None,
) -> TokenPair:
    # 密码与验证码两条路径同限流
    await check_rate_limit(
        f"user-login:{client_ip or '-'}:{phone}", max_attempts=5, window_seconds=300.0
    )
    user = (await session.execute(select(User).where(User.phone == phone))).scalar_one_or_none()
    if user is None:
        raise AppError(ErrorCode.LOGIN_FAILED, key="account.loginFailed")
    if sms_code is not None:
        try:
            await _consume_sms_code(session, phone, sms_code, "login")
        except AppError as exc:
            raise AppError(ErrorCode.LOGIN_FAILED, key="account.loginFailedSms") from exc
        await session.commit()
    elif password is not None:
        if user.password_hash is None or not verify_password(password, user.password_hash):
            raise AppError(ErrorCode.LOGIN_FAILED, key="account.loginFailedPassword")
    else:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="account.credentialRequired")
    if user.status == "frozen":
        raise AppError(
            ErrorCode.USER_FROZEN, key="account.userFrozen", http_status=status.HTTP_403_FORBIDDEN
        )
    return _issue_tokens(user)


async def refresh_tokens(session: AsyncSession, refresh_token: str) -> TokenPair:
    """轮换式刷新:refresh 一次性消费(jti 落库),重放视为泄露 → 撤销全部在外 token。"""
    payload = decode_token(refresh_token, "user", expected_type="refresh")
    user = await session.get(User, int(payload["sub"]))
    if user is None or user.status == "frozen":
        raise unauthorized()
    if payload.get("ver", 0) != user.token_version:
        raise unauthorized()
    jti = str(payload.get("jti", ""))
    inserted = (
        await session.execute(
            pg_insert(UsedRefreshToken)
            .values(
                jti=jti,
                user_id=user.id,
                expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
            )
            .on_conflict_do_nothing(index_elements=["jti"])
            .returning(UsedRefreshToken.jti)
        )
    ).scalar_one_or_none()
    if inserted is None:
        user.token_version += 1
        await session.commit()
        logger.warning("refresh_token_replayed", user_id=user.id)
        raise unauthorized()
    await session.commit()
    return _issue_tokens(user)


async def get_user(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise not_found("用户不存在")
    return user


async def submit_real_name(session: AsyncSession, user: User, name: str, id_number: str) -> User:
    """实名认证:三要素核验(姓名+身份证+账号手机号)。核验通过即 verified。

    身份证号只存脱敏串(PIPL:原文即用即弃,不落库不打日志)。
    """
    from app.modules.account.realname import (
        RealNameError,
        get_realname_provider,
        mask_id_number,
    )

    if user.verification_status == "verified":
        raise AppError(ErrorCode.CONFLICT, key="account.realNameDone")
    await check_rate_limit(f"real-name:{user.id}", max_attempts=5, window_seconds=3600.0)
    provider = await get_realname_provider(session)
    try:
        ok = await provider.verify(name, id_number, user.phone)
    except RealNameError as exc:
        # 渠道故障 ≠ 核验不一致:502 上抛
        logger.error("real_name_channel_error", user_id=user.id, error=str(exc))
        raise AppError(
            ErrorCode.REAL_NAME_CHANNEL_ERROR,
            key="account.realNameChannelError",
            http_status=status.HTTP_502_BAD_GATEWAY,
        ) from exc
    if not ok:
        raise AppError(ErrorCode.REAL_NAME_MISMATCH, key="account.realNameMismatch")
    user.id_name = name
    user.id_number = mask_id_number(id_number)
    user.verification_status = "verified"
    await session.commit()
    logger.info("real_name_verified", user_id=user.id)
    return user


async def set_warn_threshold(session: AsyncSession, user: User, hours: int) -> User:
    user.low_balance_warn_hours = hours
    await session.commit()
    return user


# ---------- SSH keys ----------


async def list_ssh_keys(session: AsyncSession, user_id: int) -> list[SshKey]:
    return list(
        (
            await session.execute(
                select(SshKey).where(SshKey.user_id == user_id).order_by(SshKey.id)
            )
        ).scalars()
    )


async def add_ssh_key(session: AsyncSession, user_id: int, name: str, public_key: str) -> SshKey:
    try:
        normalized, fingerprint = parse_public_key(public_key)
    except ValueError as exc:
        raise AppError(ErrorCode.SSH_KEY_INVALID, str(exc)) from exc
    dup = (
        await session.execute(select(SshKey).where(SshKey.fingerprint == fingerprint))
    ).scalar_one_or_none()
    if dup is not None:
        raise AppError(ErrorCode.SSH_KEY_DUPLICATE, key="account.sshKeyDuplicate")
    key = SshKey(user_id=user_id, name=name, public_key=normalized, fingerprint=fingerprint)
    session.add(key)
    await session.commit()
    await session.refresh(key)
    return key


async def delete_ssh_key(session: AsyncSession, user_id: int, key_id: int) -> None:
    key = await session.get(SshKey, key_id)
    if key is None or key.user_id != user_id:
        raise not_found("公钥不存在")
    await session.delete(key)
    await session.commit()


async def get_warn_thresholds(session: AsyncSession, user_ids: list[int]) -> dict[int, int]:
    """余额巡检用:user_id → 预警阈值小时数。"""
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
    """公告群发等场景:全部 active 用户 id。"""
    return list((await session.execute(select(User.id).where(User.status == "active"))).scalars())


async def signup_counts(session: AsyncSession, *, tz_offset_minutes: int = 0) -> dict:
    """今日/昨日新注册数(本地日界)。"""
    from sqlalchemy import func

    offset = timedelta(minutes=tz_offset_minutes)
    local_now = now_utc() + offset
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0) - offset
    prev_day_start = day_start - timedelta(days=1)

    async def _count(start, end=None) -> int:
        stmt = select(func.count()).select_from(User).where(User.created_at >= start)
        if end is not None:
            stmt = stmt.where(User.created_at < end)
        return (await session.execute(stmt)).scalar_one()

    return {
        "today_signups": await _count(day_start),
        "yesterday_signups": await _count(prev_day_start, day_start),
    }


async def admin_list_users(session: AsyncSession) -> list[User]:
    return list((await session.execute(select(User).order_by(User.id.desc()).limit(500))).scalars())


async def admin_set_user_status(session: AsyncSession, user_id: int, status_: str) -> User:
    user = await get_user(session, user_id)
    user.status = status_
    if status_ == "frozen":
        user.token_version += 1  # 冻结即撤销全部在外 token(含 refresh)
    await session.commit()
    return user
