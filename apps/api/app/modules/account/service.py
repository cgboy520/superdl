import secrets
from datetime import timedelta

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode, not_found, unauthorized
from app.core.logging import get_logger
from app.core.ratelimit import check_rate_limit
from app.core.security import create_token, decode_token, hash_password, verify_password
from app.core.timeutil import now_utc
from app.modules.account.models import SmsCode, SshKey, User
from app.modules.account.schemas import TokenPair, UserOut
from app.modules.account.sshkey_util import parse_public_key

logger = get_logger(__name__)

MOCK_SMS_CODE = "123456"


async def send_sms_code(session: AsyncSession, phone: str, purpose: str) -> None:
    settings = get_settings()
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
            f"发送过于频繁,请 {settings.sms_send_interval_seconds} 秒后再试",
            http_status=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    code = MOCK_SMS_CODE if settings.sms_provider == "mock" else f"{secrets.randbelow(10**6):06d}"
    session.add(
        SmsCode(
            phone=phone,
            code=code,
            purpose=purpose,
            expires_at=now_utc() + timedelta(seconds=settings.sms_code_ttl_seconds),
        )
    )
    await session.commit()
    if settings.sms_provider == "mock":
        logger.info("mock_sms_sent", phone=phone, purpose=purpose, code=code)
    else:  # pragma: no cover - 真实渠道 WP9 接入
        raise NotImplementedError("SMS provider not wired yet (WP9)")


async def _consume_sms_code(session: AsyncSession, phone: str, code: str, purpose: str) -> None:
    """校验并一次性消费验证码。同事务内调用,失败抛 SMS_CODE_INVALID。"""
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
    if row is None or not secrets.compare_digest(row.code, code):
        raise AppError(ErrorCode.SMS_CODE_INVALID, "验证码错误或已过期")
    row.used_at = now_utc()


def _issue_tokens(user: User) -> TokenPair:
    return TokenPair(
        access_token=create_token(str(user.id), "user", token_type="access"),
        refresh_token=create_token(str(user.id), "user", token_type="refresh"),
        user=UserOut.model_validate(user),
    )


async def register(
    session: AsyncSession, phone: str, sms_code: str, password: str | None
) -> TokenPair:
    existing = (await session.execute(select(User).where(User.phone == phone))).scalar_one_or_none()
    if existing is not None:
        raise AppError(ErrorCode.PHONE_TAKEN, "该手机号已注册,请直接登录")
    await _consume_sms_code(session, phone, sms_code, "register")
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
    if password is not None:
        check_rate_limit(
            f"user-login:{client_ip or '-'}:{phone}", max_attempts=5, window_seconds=300.0
        )
    user = (await session.execute(select(User).where(User.phone == phone))).scalar_one_or_none()
    if user is None:
        raise AppError(ErrorCode.LOGIN_FAILED, "手机号或凭证错误")
    if sms_code is not None:
        try:
            await _consume_sms_code(session, phone, sms_code, "login")
        except AppError as exc:
            raise AppError(ErrorCode.LOGIN_FAILED, "手机号或验证码错误") from exc
        await session.commit()
    elif password is not None:
        if user.password_hash is None or not verify_password(password, user.password_hash):
            raise AppError(ErrorCode.LOGIN_FAILED, "手机号或密码错误")
    else:
        raise AppError(ErrorCode.VALIDATION_ERROR, "需提供验证码或密码")
    if user.status == "frozen":
        raise AppError(
            ErrorCode.USER_FROZEN, "账号已被冻结,请联系客服", http_status=status.HTTP_403_FORBIDDEN
        )
    return _issue_tokens(user)


async def refresh_tokens(session: AsyncSession, refresh_token: str) -> TokenPair:
    payload = decode_token(refresh_token, "user", expected_type="refresh")
    user = await session.get(User, int(payload["sub"]))
    if user is None or user.status == "frozen":
        raise unauthorized()
    return _issue_tokens(user)


async def get_user(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise not_found("用户不存在")
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
        raise AppError(ErrorCode.SSH_KEY_DUPLICATE, "该公钥已添加过")
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


async def get_authorized_keys(session: AsyncSession, user_id: int) -> list[str]:
    """供 orchestrator 注入实例 authorized_keys。"""
    return [k.public_key for k in await list_ssh_keys(session, user_id)]
