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
from app.core.loginguard import LoginBucket, login_failed, login_preflight, login_succeeded
from app.core.metrics import LOGIN_FAILED_TOTAL, SMS_SENT_TOTAL, USER_SIGNUP_TOTAL
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

# 单条验证码失败次数上限,达到即作废
MAX_SMS_CODE_ATTEMPTS = 5

# 同号发送退避指数上限:第 N 条未消费验证码的间隔 = 基础间隔 × 2^min(N-1, 上限);消费后归零
SMS_SEND_BACKOFF_MAX_EXPONENT = 3

# 验证码日配额,按「消费」计(见 _consume_sms_code)
SMS_CONSUME_DAILY_MAX = 10

# 同 jti 重放宽限窗:窗内按并发重试回同一对 token;窗外判泄露,撤销全部会话
REFRESH_REPLAY_GRACE_SECONDS = 10.0


async def send_sms_code(
    session: AsyncSession,
    phone: str,
    purpose: str,
    *,
    client_ip: str | None = None,
    captcha_token: str | None = None,
) -> None:
    settings = get_settings()
    # 发送尝试只按 IP 限流;手机号日配额在消费侧计(SMS_CONSUME_DAILY_MAX)
    await check_rate_limit(
        f"sms-send-ip:{client_ip or '-'}", max_attempts=20, window_seconds=3600.0
    )
    cfg = await get_runtime_config(session)
    # 人机校验(captcha_enabled),fail-closed:渠道故障一律 502
    if cfg.captcha_enabled:
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
    # 平台级短信预算池:计数即准入,不落库无效验证码
    await ensure_sms_platform_quota()
    # 同号事务级咨询锁:「查最近→退避判定→落新码」按手机号串行;渠道发送在 commit 之后,不占锁
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:phone))"), {"phone": phone})
    # 同号递增退避:连续未消费越多间隔越长,消费一条即归零
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
    streak = 0
    for row in recent:
        if row.used_at is None:
            streak += 1
        else:
            break
    if streak:
        base = settings.sms_send_interval_seconds
        required = base * (2 ** min(streak - 1, SMS_SEND_BACKOFF_MAX_EXPONENT))
        elapsed = (now_utc() - ensure_utc(recent[0].created_at)).total_seconds()
        if elapsed < required:
            raise AppError(
                ErrorCode.SMS_TOO_FREQUENT,
                key="account.smsTooFrequent",
                params={"seconds": math.ceil(required - elapsed)},
                http_status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
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
        await channel.send(phone, cfg.sms_template_verify or "", {"code": code})
        SMS_SENT_TOTAL.labels(purpose=purpose).inc()
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

    失败计次先 commit 再抛;最新一条达到 MAX_SMS_CODE_ATTEMPTS 即作废。
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
    # candidates 兼读主密钥轮换世代(见 crypto.py);全部算完再 any,不短路
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
    # 日配额按「消费」计:只在成功分支、标 used_at 之前计数
    await check_rate_limit(
        f"sms-consume-phone:{phone}", max_attempts=SMS_CONSUME_DAILY_MAX, window_seconds=86400.0
    )
    row.used_at = now_utc()


def _issue_tokens(
    user: User,
    *,
    refresh_jti: str | None = None,
    access_jti: str | None = None,
    iat: datetime | None = None,
) -> TokenPair:
    """签发 token 对。jti/iat 仅 refresh 轮换链使用:首消费生成并落库,
    宽限窗重放按落库值重编码。"""
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
    # 先验码再判重(防手机号枚举);码行 FOR UPDATE 把同号并发注册串行化
    await _consume_sms_code(session, phone, sms_code, "register")
    existing = (await session.execute(select(User).where(User.phone == phone))).scalar_one_or_none()
    if existing is not None:
        # 已注册号重复注册,与登录失败同一条留痕线
        logger.warning(
            "user_register_failed",
            account=mask_phone_value(phone),
            ip=client_ip,
            reason="phone_taken",
        )
        raise AppError(ErrorCode.PHONE_TAKEN, key="account.phoneTaken")
    user = User(phone=phone, password_hash=await hash_password(password) if password else None)
    session.add(user)
    # 注册同意存证:terms/privacy 各一条,版本 = 当前 published,与建号同事务

    await session.flush()  # 取 user.id
    await legal_service.record_registration_consents(session, user.id, client_ip)
    await session.commit()
    await session.refresh(user)
    USER_SIGNUP_TOTAL.inc()
    logger.info("user_registered", user_id=user.id)
    return _issue_tokens(user)


# 登录限流桶(键模板, max_attempts, window_seconds),四层:ip / ip+phone / acct 15min / acct-daily。
# 四层登录桶(阈值):IP / IP+账号 / 账号 15 分钟窗 / 账号日窗;机制在 core/loginguard。
# 账号 15 分钟窗兼作异常登录判定的数据源(先读命中数再清),不走 clear_on_success。
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
    """验证码或密码二选一;两条路径时序拉平,失败一律 _LoginFailed。"""
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
    # 先结束只读事务把连接还给池:bcrypt 期间不占连接(expire_on_commit=False,user 属性仍在)
    await session.commit()
    stored = (
        user.password_hash if (user is not None and user.password_hash) else dummy_password_hash()
    )
    password_ok = await verify_password(password, stored)
    if user is None or user.password_hash is None or not password_ok:
        raise _LoginFailed(registered=user is not None)
    return user


async def _notify_login_anomaly(session: AsyncSession, user: User) -> None:
    """账号 15 分钟窗内有失败记录而本次密码登录成功 → 通知本人;之后清零该桶
    (先读后清,顺序不可换)。"""
    key = _acct_bucket_key(user.phone)
    acct_hits = await read_hits(key, window_seconds=LOGIN_ACCT_WINDOW)
    if acct_hits > 0:
        # notify.service 顶层依赖本模块(取手机号 / 活跃用户),此处只能延迟 import
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
    """密码或验证码登录。密码路径先过封禁预检;失败四层同计并留痕;成功清零配对桶并判异常登录。"""
    buckets = _login_buckets(phone, client_ip)
    if password is not None:
        await login_preflight(buckets)
    try:
        user = await _verify_credentials(session, phone, sms_code=sms_code, password=password)
    except _LoginFailed as exc:
        await login_failed(buckets)
        # 失败登录留痕(与管理端 admin_login_failed 同口径);号码显式打码,键名不用 phone
        LOGIN_FAILED_TOTAL.labels(actor_type="user").inc()
        logger.warning(
            "user_login_failed",
            account=mask_phone_value(phone),
            ip=client_ip,
            via="sms" if sms_code is not None else "password",
            registered=exc.registered,
        )
        raise
    await login_succeeded(buckets)
    if password is not None:
        await _notify_login_anomaly(session, user)
    # 已注销账号的 phone 已改写为 del:…,按手机号查不到
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

    先验码再查账号;成功后 token_version+1 撤销全部在外会话并发新 token 对。
    """
    await check_rate_limit(
        f"password-reset:{client_ip or '-'}:{phone}", max_attempts=5, window_seconds=300.0
    )
    await _consume_sms_code(session, phone, sms_code, "reset_password")
    # 行锁:token_version 读-改-写与 refresh 重放撤销/登出互斥
    user = (
        await session.execute(select(User).where(User.phone == phone).with_for_update())
    ).scalar_one_or_none()
    if user is None:
        # 验码已过但号不存在,留痕同登录失败线
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
    """轮换式刷新:refresh 一次性消费(jti 落库),重放视为泄露 → 撤销全部在外 token。

    宽限窗内同 jti 重放视为并发重试,回首消费登记的同一对 token。
    """
    payload = decode_token(refresh_token, "user", expected_type="refresh")
    # 行锁:token_version 读-改-写与改密/登出全部/并发刷新互斥
    user = await session.get(User, int(payload["sub"]), with_for_update=True)
    if user is None or user.status == "frozen":
        raise unauthorized()
    if user.status == "deleted":
        raise unauthorized(key="account.accountDeleted")
    # 撤销闸:ver 缺失一律视为不匹配(401),不得给默认值
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
                # 登出消费的重放:拒绝但不 bump token_version
                logger.warning("logout_consumed_token_replayed", user_id=user.id)
                raise unauthorized()
            if used.consumed_via == "refresh":
                if used.replaced_refresh_jti is not None and used.replaced_iat is not None:
                    # 宽限窗内重放 = 并发重试:回同一对 token
                    return _issue_tokens(
                        user,
                        refresh_jti=used.replaced_refresh_jti,
                        access_jti=used.replaced_access_jti,
                        iat=ensure_utc(used.replaced_iat),
                    )
                # 已消费但未落替代对(两笔提交之间崩溃):维持原补发语义
                return _issue_tokens(user)
        user.token_version += 1
        await session.commit()
        logger.warning("refresh_token_replayed", user_id=user.id)
        raise unauthorized()
    # 首消费:轮换结果登记到消费记录
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
    """登出当前会话:refresh token 落 used_refresh_tokens(consumed_via='logout',重放一律 401)。

    token 无效/过期/已登出也静默成功(恒回 204)。要即时全撤用 logout_all。
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
    """登出全部会话:token_version+1,已签发的 access/refresh 全部失效。"""
    # 行锁:与改密/refresh 重放撤销的 token_version 读-改-写互斥
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
        # provider 构造失败(凭据未配置)与渠道故障同属 502
        provider = await get_realname_provider(session)
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
    # 同证件绑定账号数上限(按带密钥摘要比对,兼读轮换旧世代)
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


# ---------- SSH 公钥(增删列在 sshkeys.py;这里只留建实例热路径的读取) ----------


async def ssh_keys_by_ids(session: AsyncSession, user_id: int, ids: list[int]) -> list[SshKey]:
    """本用户名下、给定 id 集合内的公钥(创建实例热路径:过滤下推到 SQL)。"""
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
    """实名闸门:real_name_required_for_recharge=true 时未实名一律 403。
    挂点:充值、创建实例、开机、续费、转包周期、建数据盘,一律经本函数。"""
    cfg = await get_runtime_config(session)
    if cfg.real_name_required_for_recharge and user.verification_status != "verified":
        raise AppError(ErrorCode.REAL_NAME_REQUIRED, key=key, http_status=403)


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
    """租户列表(游标分页)。q = 手机号:完整 11 位精确匹配,短串按后缀匹配。

    order = id 正/倒序,游标语义随方向翻转。聚合列在 Python 侧按页拼装,不支持以其排序。
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
    """被冻结的租户 id(巡检据此停掉他们仍在跑的实例)。"""
    return list((await session.execute(select(User.id).where(User.status == "frozen"))).scalars())


async def admin_set_user_status(session: AsyncSession, user_id: int, status_: str) -> User:
    """只管 users 表。不 commit,调用方把「停机」编排进同一事务。"""
    user = await get_user(session, user_id)
    user.status = status_
    if status_ == "frozen":
        user.token_version += 1  # 冻结即撤销全部在外 token
    await session.flush()
    return user


# ---------- 用户级配额覆盖 ----------


@dataclass(frozen=True)
class UserLimits:
    """每用户配额生效值(实例数 / GPU 总数 / 数据盘块数)。"""

    max_instances: int
    max_gpus: int
    max_disks: int


async def get_user_limits(session: AsyncSession, user_id: int) -> UserLimits:
    """配额校验链:用户级覆盖(user_quota_overrides)→ 平台策略(policy_overrides)→ env 默认。
    编排建实例与建盘统一经这里读。"""
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
    await get_user(session, user_id)  # 幽灵 id → 404
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
    """实名信息透出:masked=True 脱敏(默认);False 明文(仅 reveal 显式动作,调用方须落审计)。"""
    if not masked:
        return user.verification_status, user.id_name
    return user.verification_status, mask_id_name(user.id_name) if user.id_name else None
