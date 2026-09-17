import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.compliance import current_profile
from app.core.config import get_settings
from app.core.crypto import hash_kyc_identity_candidates
from app.core.errors import AppError, ErrorCode, conflict, not_found, unauthorized
from app.core.handles import Handle, mask_handle, ratelimit_key
from app.core.locale import DEFAULT_LOCALE, Locale
from app.core.logging import get_logger
from app.core.loginguard import LoginBucket, login_attempt, login_failed, login_succeeded
from app.core.metrics import LOGIN_FAILED_TOTAL, USER_SIGNUP_TOTAL
from app.core.pagination import RawPage, clamp_limit, decode_cursor_int, slice_page
from app.core.platform_config import get_runtime_config
from app.core.ratelimit import check_rate_limit, clear_rate_limit, read_hits
from app.core.regions import cn
from app.core.security import (
    create_token,
    decode_token,
    dummy_password_hash,
    hash_password,
    verify_password,
)
from app.core.sqlutil import like_escape
from app.core.timeutil import ensure_utc, local_day_range, now_utc
from app.modules.account import verification
from app.modules.account.kyc import (
    KycError,
    KycRegionUnsupported,
    KycSubject,
    get_kyc_provider,
    mask_id_name,
    mask_identity,
)
from app.modules.account.models import (
    SshKey,
    UsedRefreshToken,
    User,
    UserQuotaOverride,
)
from app.modules.account.schemas import TokenPair, UserOut
from app.modules.legal import service as legal_service

logger = get_logger(__name__)

REFRESH_REPLAY_GRACE_SECONDS = 10.0


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


async def send_verification_code(
    session: AsyncSession,
    handle: Handle,
    purpose: verification.CodePurpose,
    *,
    client_ip: str | None = None,
    captcha_token: str | None = None,
    locale: Locale = DEFAULT_LOCALE,
) -> None:
    await verification.send_code(
        session, handle, purpose, client_ip=client_ip, captcha_token=captcha_token, locale=locale
    )


async def _user_by_handle(
    session: AsyncSession, handle: Handle, *, for_update: bool = False
) -> User | None:
    column = User.email if handle.kind == "email" else User.phone
    stmt = select(User).where(column == handle.value)
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


def _check_phone_policy(phone: str | None) -> None:
    """Compliance profile: phone required / restricted to the profile's dial codes."""
    profile = current_profile()
    if phone is None:
        if profile.phone_required:
            raise AppError(ErrorCode.VALIDATION_ERROR, key="account.phoneRequired")
        return
    codes = profile.phone_dial_codes
    if codes and not any(phone.startswith(f"+{c}") for c in codes):
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="account.phoneRegionNotAllowed",
            params={"codes": ", ".join(f"+{c}" for c in codes)},
        )


async def register(
    session: AsyncSession,
    *,
    email: str,
    email_code: str,
    password: str | None,
    phone: str | None = None,
    phone_code: str | None = None,
    accept_terms: bool = False,
    client_ip: str | None = None,
) -> TokenPair:
    """Email is the primary handle; a phone is bound at sign-up only with its own SMS code, and
    only when the compliance profile allows / requires it."""
    if not accept_terms:
        raise AppError(ErrorCode.TERMS_NOT_ACCEPTED, key="account.termsNotAccepted")
    await check_rate_limit(
        f"user-register:{client_ip or '-'}:{ratelimit_key(email)}",
        max_attempts=5,
        window_seconds=300.0,
    )
    _check_phone_policy(phone)
    phone_handle = Handle("phone", phone) if phone else None
    if phone_handle is not None and not phone_code:
        raise AppError(ErrorCode.CODE_INVALID, key="account.codeInvalid")
    email_handle = Handle("email", email)
    await verification.consume_code(session, email_handle, email_code, "register")
    if phone_handle is not None:
        await verification.consume_code(session, phone_handle, phone_code or "", "register")
    for handle, key in ((email_handle, "account.emailTaken"), (phone_handle, "account.phoneTaken")):
        if handle is not None and await _user_by_handle(session, handle) is not None:
            logger.warning(
                "user_register_failed",
                account=mask_handle(handle.value),
                ip=client_ip,
                reason="handle_taken",
            )
            raise AppError(ErrorCode.HANDLE_TAKEN, key=key)
    user = User(
        email=email,
        email_verified_at=now_utc(),
        phone=phone,
        password_hash=await hash_password(password) if password else None,
    )
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


def _acct_bucket_key(handle_key: str) -> str:
    return f"user-login-acct:{handle_key}"


def _login_buckets(handle_key: str, client_ip: str | None) -> list[LoginBucket]:
    ip = client_ip or "-"
    return [
        LoginBucket(f"user-login-ip:{ip}", LOGIN_IP_MAX, LOGIN_IP_WINDOW),
        LoginBucket(
            f"user-login:{ip}:{handle_key}",
            LOGIN_PAIR_MAX,
            LOGIN_PAIR_WINDOW,
            clear_on_success=True,
        ),
        LoginBucket(_acct_bucket_key(handle_key), LOGIN_ACCT_MAX, LOGIN_ACCT_WINDOW),
        LoginBucket(
            f"user-login-acct-daily:{handle_key}", LOGIN_ACCT_DAILY_MAX, LOGIN_ACCT_DAILY_WINDOW
        ),
    ]


class _LoginFailed(AppError):
    """凭据错误(「未注册」与「凭证错」对外不可区分);registered 只进日志。"""

    def __init__(self, *, registered: bool) -> None:
        super().__init__(ErrorCode.LOGIN_FAILED, key="account.loginFailed")
        self.registered = registered


async def _verify_credentials(
    session: AsyncSession, handle: Handle, *, code: str | None, password: str | None
) -> User:
    """A code wins over a password; invalid credentials and unknown accounts both raise
    _LoginFailed (indistinguishable to the caller)."""
    user = await _user_by_handle(session, handle)
    if code is not None:
        try:
            await verification.consume_code(session, handle, code, "login")
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
    key = _acct_bucket_key(ratelimit_key(user.primary_handle))
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
    handle: Handle,
    code: str | None,
    password: str | None,
    *,
    client_ip: str | None = None,
) -> TokenPair:
    """密码或验证码登录。密码路径 bcrypt 前四层桶先计数再判定;失败留痕;
    成功退还预计数、清零配对桶并判异常登录。验证码路径只在失败后计数。"""
    buckets = _login_buckets(ratelimit_key(handle.value), client_ip)
    precounted = password is not None
    if precounted:
        await login_attempt(buckets)
    try:
        user = await _verify_credentials(session, handle, code=code, password=password)
    except _LoginFailed as exc:
        await login_failed(buckets, precounted=precounted)
        LOGIN_FAILED_TOTAL.labels(actor_type="user").inc()
        logger.warning(
            "user_login_failed",
            account=mask_handle(handle.value),
            ip=client_ip,
            via="code" if code is not None else "password",
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
    handle: Handle,
    code: str,
    new_password: str,
    *,
    client_ip: str | None = None,
) -> TokenPair:
    """Set a new password with a verification code (first set, change and recovery share this
    path). The code is checked before the account row is locked; success bumps token_version,
    revoking every other session, and issues a fresh token pair."""
    await check_rate_limit(
        f"password-reset:{client_ip or '-'}:{ratelimit_key(handle.value)}",
        max_attempts=5,
        window_seconds=300.0,
    )
    await verification.consume_code(session, handle, code, "reset_password")
    user = await _user_by_handle(session, handle, for_update=True)
    if user is None:
        logger.warning(
            "password_reset_failed",
            account=mask_handle(handle.value),
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


async def request_handle_code(
    session: AsyncSession,
    user: User,
    handle: Handle,
    *,
    client_ip: str | None = None,
    locale: Locale = DEFAULT_LOCALE,
) -> None:
    """Code for binding / replacing a handle on a signed-in account (no CAPTCHA); a phone must
    satisfy the compliance profile's dial-code rule."""
    if handle.kind == "phone":
        _check_phone_policy(handle.value)
    logger.info("handle_code_requested", user_id=user.id, kind=handle.kind)
    await verification.send_code(
        session,
        handle,
        "bind_handle",
        client_ip=client_ip,
        locale=locale,
        require_captcha=False,
    )


async def confirm_handle(session: AsyncSession, user: User, handle: Handle, code: str) -> User:
    """Consume the bind code, then set the handle if no other account owns it (409 otherwise)."""
    await verification.consume_code(session, handle, code, "bind_handle")
    owner = await _user_by_handle(session, handle)
    if owner is not None and owner.id != user.id:
        await session.commit()
        raise conflict(key="account.handleTaken")
    locked = await session.get(User, user.id, with_for_update=True)
    assert locked is not None
    if handle.kind == "email":
        locked.email = handle.value
        locked.email_verified_at = now_utc()
    else:
        locked.phone = handle.value
    await session.commit()
    await session.refresh(locked)
    logger.info("handle_bound", user_id=user.id, kind=handle.kind)
    return locked


async def remove_phone(session: AsyncSession, user: User) -> User:
    """Unbind the phone; refused when the profile requires one or when no email is bound."""
    if current_profile().phone_required:
        raise conflict(key="account.phoneRequiredByProfile")
    locked = await session.get(User, user.id, with_for_update=True)
    assert locked is not None
    if not locked.email:
        raise conflict(key="account.emailRequiredFirst")
    locked.phone = None
    await session.commit()
    await session.refresh(locked)
    logger.info("phone_removed", user_id=user.id)
    return locked


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


async def submit_kyc(
    session: AsyncSession, user: User, full_name: str, identity_number: str | None
) -> User:
    """Identity verification through the configured provider. The compliance profile selects the
    form (`cn_id_card` validates the PRC ID checksum); a pass stores the masked identity, its keyed
    digest, the provider and the reference — never the plaintext."""
    if user.kyc_status == "verified":
        raise conflict(key="account.realNameDone")
    cfg = await get_runtime_config(session)
    if not cfg.real_name_enabled:
        raise AppError(
            ErrorCode.REAL_NAME_DISABLED,
            key="account.realNameDisabled",
            http_status=status.HTTP_409_CONFLICT,
        )
    profile = current_profile()
    if profile.kyc_form is None:
        raise conflict(key="account.kycNotAvailable")
    if profile.kyc_form == "cn_id_card":
        if not identity_number or not cn.validate_id_number(identity_number):
            raise AppError(ErrorCode.VALIDATION_ERROR, key="account.kycIdentityInvalid")
        identity_number = identity_number.strip().upper()
    await check_rate_limit(f"real-name:{user.id}", max_attempts=5, window_seconds=3600.0)
    subject = KycSubject(
        user_id=user.id,
        full_name=full_name,
        identity_number=identity_number,
        phone=user.phone,
        email=user.email,
        country="CN" if profile.kyc_form == "cn_id_card" else "",
    )
    try:
        provider = await get_kyc_provider(session)
        result = await provider.verify(subject)
    except KycRegionUnsupported as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="account.kycRegionUnsupported") from exc
    except KycError as exc:
        logger.error("kyc_channel_error", user_id=user.id, error=str(exc))
        raise AppError(
            ErrorCode.REAL_NAME_CHANNEL_ERROR,
            key="account.realNameChannelError",
            http_status=status.HTTP_502_BAD_GATEWAY,
        ) from exc
    if not result.verified:
        raise AppError(ErrorCode.REAL_NAME_MISMATCH, key="account.realNameMismatch")
    identity_key = result.identity_key or identity_number or f"user:{user.id}"
    digest_candidates = hash_kyc_identity_candidates(identity_key)
    bound = (
        await session.execute(
            select(func.count()).where(
                User.kyc_identity_hmac.in_(digest_candidates),
                User.id != user.id,
                User.status != "deleted",
            )
        )
    ).scalar_one()
    max_accounts = get_settings().real_name_max_accounts_per_identity
    if bound >= max_accounts:
        logger.warning("kyc_identity_limit", user_id=user.id, bound=bound)
        raise conflict(key="account.realNameIdentityLimit", params={"max": max_accounts})
    user.kyc_name = full_name
    user.kyc_identity_masked = mask_identity(identity_number) if identity_number else None
    user.kyc_identity_hmac = digest_candidates[0]
    user.kyc_provider = result.provider
    user.kyc_ref = result.ref
    user.kyc_verified_at = now_utc()
    user.kyc_status = "verified"
    await session.commit()
    logger.info("kyc_verified", user_id=user.id, provider=result.provider)
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
    """With real_name_required_for_recharge=true, unverified users get 403 — only when the
    compliance profile offers a KYC form (no form, no gate)."""
    cfg = await get_runtime_config(session)
    if (
        cfg.real_name_required_for_recharge
        and current_profile().kyc_form is not None
        and user.kyc_status != "verified"
    ):
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
    """Tenants by id cursor; order='asc' ascending, otherwise descending.

    q (trimmed): containing `@` → exact email; starting with `+` → exact E.164 phone; digits →
    phone suffix; anything else → email prefix.
    """
    lim = clamp_limit(limit)
    ascending = order == "asc"
    stmt = select(User).order_by(User.id.asc() if ascending else User.id.desc()).limit(lim + 1)
    if status:
        stmt = stmt.where(User.status == status)
    q = (q or "").strip()
    if q:
        if "@" in q:
            stmt = stmt.where(User.email == q.lower())
        elif q.startswith("+"):
            stmt = stmt.where(User.phone == q)
        elif q.isdigit():
            stmt = stmt.where(User.phone.like(f"%{like_escape(q)}", escape="\\"))
        else:
            stmt = stmt.where(User.email.like(f"{like_escape(q.lower())}%", escape="\\"))
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
    """KYC status and name; masked=True masks the name, False returns it and the caller audits."""
    if not masked:
        return user.kyc_status, user.kyc_name
    return user.kyc_status, mask_id_name(user.kyc_name) if user.kyc_name else None
