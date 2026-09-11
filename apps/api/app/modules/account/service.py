import math
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import status
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.captcha import CaptchaError, get_captcha_channel
from app.core.config import get_settings
from app.core.constants import ADMIN_LIST_CAP
from app.core.crypto import hash_id_number_candidates, hash_sms_code, hash_sms_code_candidates
from app.core.errors import AppError, ErrorCode, conflict, not_found, unauthorized
from app.core.logging import get_logger, mask_phone_value
from app.core.metrics import LOGIN_FAILED_TOTAL, SMS_SENT_TOTAL, USER_SIGNUP_TOTAL
from app.core.money import money_str
from app.core.pagination import RawPage
from app.core.platform_config import get_effective_platform_config
from app.core.ratelimit import (
    check_rate_limit,
    clear_rate_limit,
    ensure_not_rate_limited,
    read_hits,
)
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
    AccountDeletionRequest,
    SmsCode,
    SshKey,
    UsedRefreshToken,
    User,
    UserQuotaOverride,
)
from app.modules.account.realname import mask_id_name
from app.modules.account.schemas import AdminDeletionRequestOut, TokenPair, UserOut
from app.modules.account.sshkey_util import parse_public_key

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
    cfg = await get_effective_platform_config(session)
    # 人机校验(captcha_enabled),fail-closed:渠道故障一律 502
    if cfg["captcha_enabled"] == "true":
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
    code = MOCK_SMS_CODE if cfg["sms_provider"] == "mock" else f"{secrets.randbelow(10**6):06d}"
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
        await channel.send(phone, cfg["sms_template_verify"] or "", {"code": code})
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
    from app.modules.legal import service as legal_service

    await session.flush()  # 取 user.id
    await legal_service.record_registration_consents(session, user.id, client_ip)
    await session.commit()
    await session.refresh(user)
    USER_SIGNUP_TOTAL.inc()
    logger.info("user_registered", user_id=user.id)
    return _issue_tokens(user)


# 登录限流桶(键模板, max_attempts, window_seconds),四层:ip / ip+phone / acct 15min / acct-daily。
# 预检(bcrypt 前拦封禁)/ 计数(只计失败)/ 清零 / 异常判定四处遍历同一张表。
# acct-daily 桶在用户端参与预检(管理端不参与,见 adminapi/service._login_buckets)。
_LOGIN_BUCKETS: tuple[tuple[str, int, float], ...] = (
    ("user-login-ip:{ip}", 60, 3600.0),
    ("user-login:{ip}:{phone}", 5, 300.0),
    ("user-login-acct:{phone}", 10, 900.0),
    ("user-login-acct-daily:{phone}", 30, 86400.0),
)

# 成功登录后清零的桶(IP 桶不清);acct 15min 桶兼作异常判定数据源,先读后清
_LOGIN_CLEAR_BUCKETS = ("user-login:{ip}:{phone}",)
_LOGIN_ANOMALY_BUCKET = ("user-login-acct:{phone}", 900.0)


def _login_bucket_keys(phone: str, client_ip: str | None) -> list[str]:
    return [tmpl.format(ip=client_ip or "-", phone=phone) for tmpl, _, _ in _LOGIN_BUCKETS]


async def login(
    session: AsyncSession,
    phone: str,
    sms_code: str | None,
    password: str | None,
    *,
    client_ip: str | None = None,
) -> TokenPair:
    user: User | None = None
    try:
        # 「未注册」与「凭证错」不可区分:文案统一 loginFailed,时序拉平;
        # 密码与验证码两条路径同限流
        if password is not None:
            # 封禁桶在查库与 bcrypt 之前拦下(只读预检,不计数)
            for key, (_, max_attempts, window_seconds) in zip(
                _login_bucket_keys(phone, client_ip), _LOGIN_BUCKETS, strict=True
            ):
                await ensure_not_rate_limited(
                    key, max_attempts=max_attempts, window_seconds=window_seconds
                )
        user = (await session.execute(select(User).where(User.phone == phone))).scalar_one_or_none()
        if sms_code is not None:
            try:
                await _consume_sms_code(session, phone, sms_code, "login")
            except AppError as exc:
                raise AppError(ErrorCode.LOGIN_FAILED, key="account.loginFailed") from exc
            if user is None:
                raise AppError(ErrorCode.LOGIN_FAILED, key="account.loginFailed")
            await session.commit()
        elif password is not None:
            # 先结束只读事务把连接还给池:bcrypt 期间不占连接(expire_on_commit=False,user 属性仍在)
            await session.commit()
            stored = (
                user.password_hash
                if (user is not None and user.password_hash)
                else dummy_password_hash()
            )
            password_ok = await verify_password(password, stored)
            if user is None or user.password_hash is None or not password_ok:
                raise AppError(ErrorCode.LOGIN_FAILED, key="account.loginFailed")
        else:
            raise AppError(ErrorCode.VALIDATION_ERROR, key="account.credentialRequired")
    except AppError as exc:
        if exc.code == ErrorCode.LOGIN_FAILED:
            # 只在失败后计数
            for key, (_, max_attempts, window_seconds) in zip(
                _login_bucket_keys(phone, client_ip), _LOGIN_BUCKETS, strict=True
            ):
                await check_rate_limit(
                    key, max_attempts=max_attempts, window_seconds=window_seconds
                )
            # 失败登录留痕(与管理端 admin_login_failed 同口径);号码显式打码,键名不用 phone
            LOGIN_FAILED_TOTAL.labels(actor_type="user").inc()
            logger.warning(
                "user_login_failed",
                account=mask_phone_value(phone),
                ip=client_ip,
                via="sms" if sms_code is not None else "password",
                registered=user is not None,
            )
        raise
    # 凭据正确即清零账号桶
    for tmpl in _LOGIN_CLEAR_BUCKETS:
        await clear_rate_limit(tmpl.format(ip=client_ip or "-", phone=phone))
    # 异常登录通知:账号桶窗口内有失败记录而本次成功 → 通知本人,再清零账号桶
    if password is not None:
        anomaly_tmpl, anomaly_window = _LOGIN_ANOMALY_BUCKET
        acct_hits = await read_hits(
            anomaly_tmpl.format(ip=client_ip or "-", phone=phone), window_seconds=anomaly_window
        )
        if acct_hits > 0:
            from app.modules.notify import service as notify_service

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
        # 先读 hits 再清,顺序不可换
        await clear_rate_limit(_LOGIN_ANOMALY_BUCKET[0].format(ip=client_ip or "-", phone=phone))
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
    from app.modules.account.realname import (
        RealNameError,
        get_realname_provider,
        mask_id_number,
    )

    if user.verification_status == "verified":
        raise conflict(key="account.realNameDone")
    cfg = await get_effective_platform_config(session)
    if cfg["real_name_enabled"] != "true":
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


# ---------- SSH keys ----------


async def list_ssh_keys(session: AsyncSession, user_id: int) -> list[SshKey]:
    return list(
        (
            await session.execute(
                select(SshKey).where(SshKey.user_id == user_id).order_by(SshKey.id)
            )
        ).scalars()
    )


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


MAX_SSH_KEYS_PER_USER = 50


async def add_ssh_key(session: AsyncSession, user_id: int, name: str, public_key: str) -> SshKey:
    await check_rate_limit(f"ssh-key-add:{user_id}", max_attempts=20, window_seconds=3600.0)
    try:
        normalized, fingerprint = parse_public_key(public_key)
    except ValueError as exc:
        raise AppError(ErrorCode.SSH_KEY_INVALID, str(exc)) from exc
    # 查重按本用户口径
    dup = (
        await session.execute(
            select(SshKey).where(SshKey.user_id == user_id, SshKey.fingerprint == fingerprint)
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise AppError(ErrorCode.SSH_KEY_DUPLICATE, key="account.sshKeyDuplicate")
    count = (
        await session.execute(select(func.count()).where(SshKey.user_id == user_id))
    ).scalar_one()
    if count >= MAX_SSH_KEYS_PER_USER:
        raise conflict(key="account.sshKeyLimitReached", params={"max": MAX_SSH_KEYS_PER_USER})
    key = SshKey(user_id=user_id, name=name, public_key=normalized, fingerprint=fingerprint)
    session.add(key)
    await session.commit()
    await session.refresh(key)
    return key


async def delete_ssh_key(session: AsyncSession, user_id: int, key_id: int) -> None:
    key = await session.get(SshKey, key_id)
    if key is None or key.user_id != user_id:
        raise not_found()
    await session.delete(key)
    # 同步摘除该用户未释放实例上的 authorized_keys 快照(实例行是开机下发源);运行中实例下次重启才生效
    from app.modules.orchestrator import service as orchestrator_service

    stripped = await orchestrator_service.strip_ssh_key_from_instances(
        session, user_id, key.public_key
    )
    await session.commit()
    if stripped:
        logger.info("ssh_key_stripped_from_instances", user_id=user_id, instances=stripped)


async def is_active_user(session: AsyncSession, user_id: int) -> bool:
    """归属校验:user_id 存在且 active。"""
    status = await session.scalar(select(User.status).where(User.id == user_id))
    return status == "active"


async def require_real_name_if_required(session: AsyncSession, user: User, *, key: str) -> None:
    """实名闸门:real_name_required_for_recharge=true 时未实名一律 403。
    挂点:充值、创建实例、开机、续费、转包周期、建数据盘,一律经本函数。"""
    cfg = await get_effective_platform_config(session)
    if cfg["real_name_required_for_recharge"] == "true" and user.verification_status != "verified":
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
    from sqlalchemy import func

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
    from app.core.pagination import clamp_limit, decode_cursor_int, slice_page

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
    from app.core.policies import get_effective_policies

    policies = await get_effective_policies(session)
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


# ---------- 账号注销 ----------


async def _pending_deletion_of_user(
    session: AsyncSession, user_id: int
) -> AccountDeletionRequest | None:
    return (
        await session.execute(
            select(AccountDeletionRequest).where(
                AccountDeletionRequest.user_id == user_id,
                AccountDeletionRequest.status == "pending",
            )
        )
    ).scalar_one_or_none()


async def request_deletion(
    session: AsyncSession, user: User, *, phone: str, reason: str
) -> AccountDeletionRequest:
    """申请注销(7 天冷静期)。幂等:已有 pending 直接返回既有(部分唯一索引兜底并发)。"""
    if user.phone != phone:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="account.deletionPhoneMismatch")
    existing = await _pending_deletion_of_user(session, user.id)
    if existing is not None:
        return existing
    req = AccountDeletionRequest(user_id=user.id, reason=reason)
    session.add(req)
    await session.commit()
    await session.refresh(req)
    logger.info("deletion_requested", user_id=user.id)
    return req


async def get_my_deletion_request(
    session: AsyncSession, user_id: int
) -> AccountDeletionRequest | None:
    """当前 pending;无则最近一条。"""
    pending = await _pending_deletion_of_user(session, user_id)
    if pending is not None:
        return pending
    return (
        await session.execute(
            select(AccountDeletionRequest)
            .where(AccountDeletionRequest.user_id == user_id)
            .order_by(AccountDeletionRequest.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def cancel_deletion_request(session: AsyncSession, user_id: int) -> AccountDeletionRequest:
    """冷静期内撤销。仅 pending 可撤;终态 409。"""
    req = (
        await session.execute(
            select(AccountDeletionRequest)
            .where(AccountDeletionRequest.user_id == user_id)
            .order_by(AccountDeletionRequest.id.desc())
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if req is None:
        raise not_found()
    if req.status != "pending":
        raise conflict(key="account.deletionNotCancellable", params={"status": req.status})
    req.status = "cancelled"
    await session.commit()
    logger.info("deletion_cancelled", user_id=user_id)
    return req


# ---------- 账号注销:管理端 ----------


def _deletion_out(
    req: AccountDeletionRequest,
    user: User,
    leftover_counts: dict[str, int],
    balance: Decimal,
) -> AdminDeletionRequestOut:
    return AdminDeletionRequestOut(
        id=req.id,
        user_id=req.user_id,
        phone_masked=mask_phone_value(user.phone),
        status=req.status,
        reason=req.reason,
        requested_at=req.requested_at,
        cooldown_ends_at=req.cooldown_ends_at,
        processed_by=req.processed_by,
        processed_at=req.processed_at,
        note=req.note,
        instances_active=leftover_counts["instances"],
        disks_active=leftover_counts["disks"],
        balance=money_str(balance),
    )


async def admin_list_deletion_requests(
    session: AsyncSession, status_: str | None = None
) -> list[AdminDeletionRequestOut]:
    """注销申请列表(固定截断),行内附执行前校验计数。"""
    # 必须延迟 import:orchestrator.service 与本模块循环依赖
    from app.modules.billing import service as billing_service
    from app.modules.orchestrator import service as orchestrator_service

    stmt = (
        select(AccountDeletionRequest)
        .order_by(AccountDeletionRequest.id.desc())
        .limit(ADMIN_LIST_CAP)
    )
    if status_:
        stmt = stmt.where(AccountDeletionRequest.status == status_)
    rows = list((await session.execute(stmt)).scalars())
    if not rows:
        return []
    user_ids = [r.user_id for r in rows]
    users = {
        u.id: u
        for u in (await session.execute(select(User).where(User.id.in_(user_ids)))).scalars()
    }
    counts = await orchestrator_service.deletion_leftover_counts(session, user_ids)
    balances = await billing_service.balances_by_user(session, user_ids)
    return [
        _deletion_out(
            r,
            users[r.user_id],
            counts.get(r.user_id, {"instances": 0, "disks": 0}),
            balances.get(r.user_id, Decimal("0.00")),
        )
        for r in rows
        if r.user_id in users
    ]


async def admin_get_deletion_out(session: AsyncSession, request_id: int) -> AdminDeletionRequestOut:
    """单条注销申请的管理端视图(approve/reject 响应复用)。"""
    # 必须延迟 import:orchestrator.service 与本模块循环依赖
    from app.modules.billing import service as billing_service
    from app.modules.orchestrator import service as orchestrator_service

    req = await session.get(AccountDeletionRequest, request_id)
    if req is None:
        raise not_found()
    user = await get_user(session, req.user_id)
    counts = await orchestrator_service.deletion_leftover_counts(session, [req.user_id])
    balance = await billing_service.get_balance(session, req.user_id)
    return _deletion_out(
        req,
        user,
        counts.get(req.user_id, {"instances": 0, "disks": 0}),
        balance,
    )


async def _get_deletion_for_update(
    session: AsyncSession, request_id: int
) -> AccountDeletionRequest:
    req = await session.get(AccountDeletionRequest, request_id, with_for_update=True)
    if req is None:
        raise not_found()
    return req


def _auto_reject_deletion(req: AccountDeletionRequest, *, admin_id: int, note: str) -> None:
    """执行前校验不过的自动驳回:残留清单/余额写进 note。"""
    req.status = "rejected"
    req.processed_by = admin_id
    req.processed_at = now_utc()
    req.note = note


async def approve_deletion(
    session: AsyncSession, request_id: int, *, admin_id: int
) -> AccountDeletionRequest:
    """执行注销(仅超管)。冷静期未满 409;残留资源/余额非零 → 自动驳回 + 409;
    全通过则同事务匿名化:手机号改写为随机占位串、实名字段清空、token_version+1、status=deleted。
    """
    # 必须延迟 import:orchestrator.service 与本模块循环依赖
    from app.modules.billing import service as billing_service
    from app.modules.orchestrator import service as orchestrator_service

    req = await _get_deletion_for_update(session, request_id)
    if req.status != "pending":
        raise conflict(key="account.deletionNotPending", params={"status": req.status})
    remaining = req.cooldown_ends_at - now_utc()
    if remaining.total_seconds() > 0:
        # 冷静期未满:不可执行但不驳回
        raise conflict(
            key="account.deletionCooldown",
            params={"hours": math.ceil(remaining.total_seconds() / 3600)},
        )
    # 行锁用户:匿名化与登录/refresh 的 token_version 读-改-写互斥
    user = await session.get(User, req.user_id, with_for_update=True)
    if user is None:
        raise not_found()
    leftovers = await orchestrator_service.deletion_leftovers(session, user.id)
    if leftovers["instances"] or leftovers["disks"]:
        _auto_reject_deletion(
            req,
            admin_id=admin_id,
            note=(
                f"自动驳回:名下仍有未释放实例 {len(leftovers['instances'])} 台"
                f"({', '.join(leftovers['instances'])})、未删除数据盘 {len(leftovers['disks'])} 块"
                f"({', '.join(leftovers['disks'])});请先清空资源后重新申请"
            ),
        )
        await session.commit()
        raise conflict(
            key="account.deletionLeftovers",
            params={
                "instances": len(leftovers["instances"]),
                "disks": len(leftovers["disks"]),
            },
            detail=leftovers,
        )
    balance = await billing_service.get_balance(session, user.id)
    if balance != 0:
        _auto_reject_deletion(
            req,
            admin_id=admin_id,
            note=f"自动驳回:余额 ¥{money_str(balance)} 未提现,请先经退款流程提现,到账后重新申请",
        )
        await session.commit()
        raise conflict(
            key="account.deletionBalanceRemaining",
            params={"balance": money_str(balance)},
            detail={"balance": money_str(balance)},
        )
    # 匿名化:手机号替换为随机占位串(与原号码无函数关系,最长 31 字符)、身份字段清空、全撤登录态;
    # balance_ledger/账单保留不动
    user.phone = f"del:{user.id}:{secrets.token_hex(8)}"
    user.id_name = None
    user.id_number = None
    user.verification_status = "unverified"
    user.token_version += 1
    user.status = "deleted"
    req.status = "completed"
    req.processed_by = admin_id
    req.processed_at = now_utc()
    await session.commit()
    logger.info("account_deleted", user_id=user.id)
    return req


async def reject_deletion(
    session: AsyncSession, request_id: int, *, admin_id: int, note: str
) -> AccountDeletionRequest:
    """驳回注销申请(理由必填,不受冷静期限制)。"""
    req = await _get_deletion_for_update(session, request_id)
    if req.status != "pending":
        raise conflict(key="account.deletionNotPending", params={"status": req.status})
    req.status = "rejected"
    req.processed_by = admin_id
    req.processed_at = now_utc()
    req.note = note
    await session.commit()
    logger.info("deletion_rejected", request_id=request_id)
    return req
