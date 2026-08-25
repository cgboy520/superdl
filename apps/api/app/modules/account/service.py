import hashlib
import math
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import status
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.captcha import CaptchaError, get_captcha_channel
from app.core.config import get_settings
from app.core.crypto import hash_sms_code
from app.core.db import get_sessionmaker
from app.core.errors import AppError, ErrorCode, not_found, unauthorized
from app.core.logging import get_logger
from app.core.pagination import RawPage
from app.core.platform_config import get_effective_platform_config
from app.core.ratelimit import (
    RateLimitCounter,
    check_rate_limit,
    ensure_not_rate_limited,
    read_hits,
)
from app.core.security import (
    create_token,
    decode_token,
    hash_password,
    hash_password_sync,
    verify_password,
)
from app.core.sms import SmsError, ensure_sms_platform_quota, get_sms_channel
from app.core.sqlutil import like_escape
from app.core.timeutil import ensure_utc, now_utc
from app.modules.account.models import (
    AccountDeletionRequest,
    SmsCode,
    SshKey,
    UsedRefreshToken,
    User,
    UserQuotaOverride,
)
from app.modules.account.realname import mask_company_name, mask_id_name, mask_phone
from app.modules.account.schemas import AdminDeletionRequestOut, TokenPair, UserOut
from app.modules.account.sshkey_util import parse_public_key

logger = get_logger(__name__)

MOCK_SMS_CODE = "123456"

# 未注册的手机号也走一次哈希校验,拉平时间侧信道(管理端登录同款)
_DUMMY_HASH = hash_password_sync("dummy-timing-equalizer")

# 单条验证码最多允许失败次数,达到即作废
MAX_SMS_CODE_ATTEMPTS = 5

# 同号发送退避的指数上限:连续第 N 条未消费验证码的间隔 = 基础间隔 × 2^min(N-1, 上限)
# (60s 基础间隔 → 60s/120s/240s,封顶 480s);验证码被正常消费后连续计数归零。
SMS_SEND_BACKOFF_MAX_EXPONENT = 3

# 验证码日配额,按「消费」计(见 _consume_sms_code):只有真正读到码并完成登录/注册/重置的
# 一方才计数,替他人请求验证码耗不到该配额
SMS_CONSUME_DAILY_MAX = 10

# 同 jti 重放宽限窗:窗内视为并发重试,按正常轮换处理;窗外判泄露并撤销全部会话
REFRESH_REPLAY_GRACE_SECONDS = 10.0


async def _clear_login_failures(key: str) -> None:
    """登录成功清零该桶的失败计数(独立事务,不随业务 session 回滚)。"""
    async with get_sessionmaker()() as session:
        await session.execute(delete(RateLimitCounter).where(RateLimitCounter.key == key))
        await session.commit()


async def send_sms_code(
    session: AsyncSession,
    phone: str,
    purpose: str,
    *,
    client_ip: str | None = None,
    captcha_token: str | None = None,
) -> None:
    settings = get_settings()
    # 发送尝试只按 IP 限流;手机号日配额在消费侧计(见 SMS_CONSUME_DAILY_MAX)。
    await check_rate_limit(
        f"sms-send-ip:{client_ip or '-'}", max_attempts=20, window_seconds=3600.0
    )
    # 人机校验(P1-17):分布式脚本可轮换 IP/号码池绕过全部单点限流,
    # 行为验证码是唯一纵深。闸门 fail-closed:渠道故障一律 502,宁停服务不放轰炸。
    if captcha_token is None:
        raise AppError(ErrorCode.CAPTCHA_REQUIRED, key="account.captchaRequired")
    try:
        captcha_ok = await (await get_captcha_channel(session)).verify(captcha_token, client_ip)
    except CaptchaError as exc:
        logger.error("captcha_channel_error", error=str(exc))
        raise AppError(
            ErrorCode.CAPTCHA_CHANNEL_ERROR,
            key="account.captchaChannelError",
            http_status=status.HTTP_502_BAD_GATEWAY,
        ) from exc
    if not captcha_ok:
        raise AppError(ErrorCode.CAPTCHA_VERIFY_FAILED, key="account.captchaVerifyFailed")
    # 平台级闸门:分布式 IP/号码池可绕过单点限流,预算池兜底(计数即准入,不落库无效验证码)
    await ensure_sms_platform_quota()
    # 同号递增退避:连续未消费的验证码越多,下一条允许发送的间隔越长;消费一条即归零。
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
    cfg = await get_effective_platform_config(session)
    code = MOCK_SMS_CODE if cfg["sms_provider"] == "mock" else f"{secrets.randbelow(10**6):06d}"
    row = SmsCode(
        phone=phone,
        code_hash=hash_sms_code(phone, purpose, code),  # 明文只活在这个局部变量里
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
        # 手机号明文不进集中日志(Loki 180 天 PII 面);logging 管道另有全局兜底
        logger.error("sms_send_failed", phone=mask_phone(phone), error=str(exc))
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
    expected = hash_sms_code(phone, purpose, code)
    if not secrets.compare_digest(row.code_hash, expected):
        row.attempts += 1
        if row.attempts >= MAX_SMS_CODE_ATTEMPTS:
            # 达上限即作废:否则 used_at 恒空,这条已烧毁的码会被反复选中
            row.used_at = now_utc()
        await session.commit()
        raise AppError(ErrorCode.SMS_CODE_INVALID, key="account.smsCodeInvalid")
    # 日配额按「消费」计:放在成功分支、标 used_at 之前,失败尝试与超额请求都不消耗配额
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
    """签发 token 对。jti/iat 仅 refresh 轮换链使用:首消费显式生成并落库,
    宽限窗重放按落库值重编码出同一对 token。"""
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
    # 先验码再判重:反过来就是手机号枚举 oracle
    await _consume_sms_code(session, phone, sms_code, "register")
    existing = (await session.execute(select(User).where(User.phone == phone))).scalar_one_or_none()
    if existing is not None:
        raise AppError(ErrorCode.PHONE_TAKEN, key="account.phoneTaken")
    user = User(phone=phone, password_hash=await hash_password(password) if password else None)
    session.add(user)
    # 注册必勾落证(合规举证):terms/privacy 各一条,版本=当前 published,与建号同事务
    from app.modules.legal import service as legal_service

    try:
        await session.flush()  # 取 user.id 供同意存证;并发同号在此撞唯一约束
        await legal_service.record_registration_consents(session, user.id, client_ip)
        await session.commit()
    except IntegrityError as exc:
        # 并发同号注册(双击/重试):先 SELECT 后 INSERT 的竞态由唯一约束兜底
        await session.rollback()
        raise AppError(ErrorCode.PHONE_TAKEN, key="account.phoneTaken") from exc
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
    user = (await session.execute(select(User).where(User.phone == phone))).scalar_one_or_none()
    try:
        # 「未注册」与「已注册但凭证错」不可区分:文案统一 loginFailed,
        # 时序也拉平(未注册路径照付 bcrypt 的 ~200ms);密码与验证码两条路径同限流
        if sms_code is not None:
            try:
                await _consume_sms_code(session, phone, sms_code, "login")
            except AppError as exc:
                raise AppError(ErrorCode.LOGIN_FAILED, key="account.loginFailed") from exc
            if user is None:
                raise AppError(ErrorCode.LOGIN_FAILED, key="account.loginFailed")
            await session.commit()
        elif password is not None:
            # 已封禁的桶在 bcrypt(~200ms CPU/次)之前拦下:封禁期内的撞库请求
            # 不再付哈希成本(只读预检,不计数,不影响正常登录的配额语义)
            await ensure_not_rate_limited(
                f"user-login-ip:{client_ip or '-'}", max_attempts=60, window_seconds=3600.0
            )
            await ensure_not_rate_limited(
                f"user-login:{client_ip or '-'}:{phone}", max_attempts=5, window_seconds=300.0
            )
            # 纯账号维度:撞库可以换 IP,但换不了目标账号——
            # 只按 IP+账号的桶在 N 个源地址下是 5×N 次/5 分钟,必须有账号级锁定
            await ensure_not_rate_limited(
                f"user-login-acct:{phone}", max_attempts=10, window_seconds=900.0
            )
            await ensure_not_rate_limited(
                f"user-login-acct-daily:{phone}", max_attempts=30, window_seconds=86400.0
            )
            stored = (
                user.password_hash if (user is not None and user.password_hash) else _DUMMY_HASH
            )
            password_ok = await verify_password(password, stored)
            if user is None or user.password_hash is None or not password_ok:
                raise AppError(ErrorCode.LOGIN_FAILED, key="account.loginFailed")
        else:
            raise AppError(ErrorCode.VALIDATION_ERROR, key="account.credentialRequired")
    except AppError as exc:
        if exc.code == ErrorCode.LOGIN_FAILED:
            # 只在失败后计数,成功登录不消耗配额;
            # 含手机号的键遍历号段即换桶,故再加一个只按 IP 切分的桶
            await check_rate_limit(
                f"user-login-ip:{client_ip or '-'}", max_attempts=60, window_seconds=3600.0
            )
            await check_rate_limit(
                f"user-login:{client_ip or '-'}:{phone}", max_attempts=5, window_seconds=300.0
            )
            # 账号维度同计(15 分钟窗 + 日窗阶梯):换 IP 也逃不掉目标账号的锁定
            await check_rate_limit(
                f"user-login-acct:{phone}", max_attempts=10, window_seconds=900.0
            )
            await check_rate_limit(
                f"user-login-acct-daily:{phone}", max_attempts=30, window_seconds=86400.0
            )
        raise
    # 凭据正确即清零该账号桶的失败计数(IP 桶不清:撞库不会产生成功登录)
    await _clear_login_failures(f"user-login:{client_ip or '-'}:{phone}")
    # 异常登录通知:账号桶在窗口内有失败记录而本次成功——疑似被撞库,通知本人;
    # 随后清零账号桶(正常用户的预算不被攻击者的失败计数拖垮)
    if user is not None and password is not None:
        acct_hits = await read_hits(f"user-login-acct:{phone}", window_seconds=900.0)
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
            await session.commit()  # 通知落库(password 路径此前无提交点)
        await _clear_login_failures(f"user-login-acct:{phone}")
    # 已注销账号的 phone 已改写为 del:…,按手机号查不到,不必再判 deleted(持凭证路径见 deps/refresh)
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

    先验码再查账号:反过来是手机号枚举 oracle。
    成功后 token_version+1 撤销全部在外会话,并给调用方发一对新 token。
    """
    await check_rate_limit(
        f"password-reset:{client_ip or '-'}:{phone}", max_attempts=5, window_seconds=300.0
    )
    await _consume_sms_code(session, phone, sms_code, "reset_password")
    # 行锁:token_version 读-改-写与 refresh 重放撤销/登出全部互斥,防并发丢更新
    user = (
        await session.execute(select(User).where(User.phone == phone).with_for_update())
    ).scalar_one_or_none()
    if user is None:
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

    宽限窗:同 jti 在 REFRESH_REPLAY_GRACE_SECONDS 内被重复消费视为并发重试
    (多标签页/客户端网络重试),回首消费事务登记的同一对 token——不为同一旧 token
    另开第二条长期有效的轮换链。
    """
    payload = decode_token(refresh_token, "user", expected_type="refresh")
    # 行锁:token_version 读-改-写与改密/登出全部/并发刷新互斥,防并发丢更新
    user = await session.get(User, int(payload["sub"]), with_for_update=True)
    if user is None or user.status == "frozen":
        raise unauthorized()
    if user.status == "deleted":
        raise unauthorized(key="account.accountDeleted")
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
        used = await session.get(UsedRefreshToken, jti)
        if used is not None and used.used_at > now_utc() - timedelta(
            seconds=REFRESH_REPLAY_GRACE_SECONDS
        ):
            if used.replaced_refresh_jti is not None and used.replaced_iat is not None:
                # 宽限窗内的重放 = 并发重试:回首次轮换的同一对 token,不另开有效链
                return _issue_tokens(
                    user,
                    refresh_jti=used.replaced_refresh_jti,
                    access_jti=used.replaced_access_jti,
                    iat=ensure_utc(used.replaced_iat),
                )
            # 登出写入的消费记录没有替代对:维持原补发语义(见 logout 的已知竞态说明)
            return _issue_tokens(user)
        user.token_version += 1
        await session.commit()
        logger.warning("refresh_token_replayed", user_id=user.id)
        raise unauthorized()
    # 首消费:轮换结果登记到消费记录,宽限窗重放据此回同一对 token
    new_refresh_jti = secrets.token_hex(16)
    new_access_jti = secrets.token_hex(16)
    issued_at = now_utc()
    consumed = await session.get(UsedRefreshToken, jti)
    assert consumed is not None  # 本事务刚插入
    consumed.replaced_refresh_jti = new_refresh_jti
    consumed.replaced_access_jti = new_access_jti
    consumed.replaced_iat = issued_at
    await session.commit()
    return _issue_tokens(
        user, refresh_jti=new_refresh_jti, access_jti=new_access_jti, iat=issued_at
    )


async def logout(session: AsyncSession, refresh_token: str) -> None:
    """登出当前会话:refresh token 落 used_refresh_tokens(与轮换同一条一次性消费位)。

    token 无效/过期/已登出也静默成功(调用方恒回 204),不构成 token 有效性探测口。
    已知竞态:登出与同 jti 的并发刷新撞在宽限窗内时,刷新方按并发重试放行;
    登出本就不是即时全局失效(access token 尚有短 TTL),要即时全撤用 logout_all。
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
            user_id=user_id,
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
        .on_conflict_do_nothing(index_elements=["jti"])
    )
    await session.commit()


async def logout_all(session: AsyncSession, user_id: int) -> None:
    """登出全部会话:token_version+1,已签发的 access/refresh 全部失效。"""
    # 行锁:与改密/refresh 重放撤销的 token_version 读-改-写互斥(同 _get_admin 写法)
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
    try:
        # 取 provider 也可能失败(凭据未配置):与渠道故障同属 502,不能漏成 500
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
    # 查重按本用户口径:同一把钥匙不同租户各自可添加,同一用户重复添加才拒绝。
    dup = (
        await session.execute(
            select(SshKey).where(SshKey.user_id == user_id, SshKey.fingerprint == fingerprint)
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise AppError(ErrorCode.SSH_KEY_DUPLICATE, key="account.sshKeyDuplicate")
    key = SshKey(user_id=user_id, name=name, public_key=normalized, fingerprint=fingerprint)
    session.add(key)
    try:
        await session.commit()
    except IntegrityError as exc:
        # 并发同用户同指纹:唯一约束 (user_id, fingerprint) 兜底,按重复处理而非 500
        await session.rollback()
        raise AppError(ErrorCode.SSH_KEY_DUPLICATE, key="account.sshKeyDuplicate") from exc
    await session.refresh(key)
    return key


async def delete_ssh_key(session: AsyncSession, user_id: int, key_id: int) -> None:
    key = await session.get(SshKey, key_id)
    if key is None or key.user_id != user_id:
        raise not_found()
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


async def admin_list_users(
    session: AsyncSession,
    *,
    q: str | None = None,
    status: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> RawPage[User]:
    """租户列表(游标分页,降序)。q = 手机号:完整 11 位精确匹配走唯一索引,短串按后缀匹配。"""
    from app.core.pagination import clamp_limit, decode_cursor_int, slice_page

    lim = clamp_limit(limit)
    stmt = select(User).order_by(User.id.desc()).limit(lim + 1)
    if status:
        stmt = stmt.where(User.status == status)
    q = (q or "").strip()
    if q:
        if len(q) >= 11:
            stmt = stmt.where(User.phone == q)
        else:
            # LIKE 元字符转义:q="%" / "_" 不匹配任意字符(否则一个 % 即拖全表)
            stmt = stmt.where(User.phone.like(f"%{like_escape(q)}", escape="\\"))
    last_id = decode_cursor_int(cursor)
    if last_id is not None:
        stmt = stmt.where(User.id < last_id)
    rows = list((await session.execute(stmt)).scalars())
    page_items, next_cursor = slice_page(rows, lim, key=lambda r: r.id)
    return RawPage(items=page_items, next_cursor=next_cursor)


async def frozen_user_ids(session: AsyncSession) -> list[int]:
    """被冻结的租户 id(巡检据此停掉他们仍在跑的实例)。"""
    return list((await session.execute(select(User.id).where(User.status == "frozen"))).scalars())


async def admin_set_user_status(session: AsyncSession, user_id: int, status_: str) -> User:
    """只管 users 表(账号模块的边界)。不 commit:调用方把「停机」编排进同一事务。"""
    user = await get_user(session, user_id)
    user.status = status_
    if status_ == "frozen":
        user.token_version += 1  # 冻结即撤销全部在外 token(含 refresh)
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

    编排建实例与建盘统一经这里读,不要在调用点散读 settings。
    """
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
    """写覆盖(upsert);三项全 None = 清除覆盖恢复默认链。不 commit,由调用方与审计同事务提交。"""
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


def realname_view(user: User, *, masked: bool) -> tuple[str, str | None, str | None]:
    """实名信息透出:masked=True(readonly)脱敏;False 明文(调用方须对本次敏感读落审计)。"""
    if not masked:
        return user.verification_status, user.id_name, user.company_name
    return (
        user.verification_status,
        mask_id_name(user.id_name) if user.id_name else None,
        mask_company_name(user.company_name) if user.company_name else None,
    )


# ---------- 账号注销 ----------

# 管理端注销申请列表固定截断(与 tickets/refunds 同款)
ADMIN_DELETION_LIST_CAP = 200


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
    """申请注销(进 7 天冷静期)。幂等:已有 pending 直接返回既有;
    部分唯一索引兜底并发双击,撞索引即返回胜出方。"""
    if user.phone != phone:
        # 键入手机号须与账号一致:防误触/防会话劫持者直接销号
        raise AppError(ErrorCode.VALIDATION_ERROR, key="account.deletionPhoneMismatch")
    existing = await _pending_deletion_of_user(session, user.id)
    if existing is not None:
        return existing
    req = AccountDeletionRequest(user_id=user.id, reason=reason)
    session.add(req)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        winner = await _pending_deletion_of_user(session, user.id)
        if winner is not None:
            return winner
        raise
    await session.refresh(req)
    logger.info("deletion_requested", user_id=user.id)
    return req


async def get_my_deletion_request(
    session: AsyncSession, user_id: int
) -> AccountDeletionRequest | None:
    """当前 pending;无则最近一条(卡片据此展示驳回原因/已完成/冷静期倒计时)。"""
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
    """冷静期内撤销。仅 pending 可撤;终态(rejected/completed/cancelled)409。"""
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
        raise AppError(
            ErrorCode.CONFLICT,
            key="account.deletionNotCancellable",
            params={"status": req.status},
            http_status=status.HTTP_409_CONFLICT,
        )
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
        phone_masked=mask_phone(user.phone),
        status=req.status,
        reason=req.reason,
        requested_at=req.requested_at,
        cooldown_ends_at=req.cooldown_ends_at,
        processed_by=req.processed_by,
        processed_at=req.processed_at,
        note=req.note,
        instances_active=leftover_counts["instances"],
        disks_active=leftover_counts["disks"],
        balance=format(balance, "f"),
    )


async def admin_list_deletion_requests(
    session: AsyncSession, status_: str | None = None
) -> list[AdminDeletionRequestOut]:
    """注销申请列表(固定截断)。行内附执行前校验计数,确认弹窗直接渲染。"""
    # 延迟 import 防循环:orchestrator.service → account.service
    from app.modules.billing import service as billing_service
    from app.modules.orchestrator import service as orchestrator_service

    stmt = (
        select(AccountDeletionRequest)
        .order_by(AccountDeletionRequest.id.desc())
        .limit(ADMIN_DELETION_LIST_CAP)
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
    """单条注销申请的管理端视图(approve/reject 响应复用,实时校验计数)。"""
    # 延迟 import 防循环:orchestrator.service → account.service
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
    """执行前校验不过的自动驳回:残留清单/余额引导写进 note,用户端可见。"""
    req.status = "rejected"
    req.processed_by = admin_id
    req.processed_at = now_utc()
    req.note = note


async def approve_deletion(
    session: AsyncSession, request_id: int, *, admin_id: int
) -> AccountDeletionRequest:
    """执行注销(仅超管)。冷静期未满 409;残留资源/余额非零 → 自动驳回 + 409(清单);
    全通过则同事务匿名化:手机号哈希化、实名/企业字段清空、token_version+1、status=deleted。
    """
    # 延迟 import 防循环:orchestrator.service → account.service
    from app.modules.billing import service as billing_service
    from app.modules.orchestrator import service as orchestrator_service

    req = await _get_deletion_for_update(session, request_id)
    if req.status != "pending":
        raise AppError(
            ErrorCode.CONFLICT,
            key="account.deletionNotPending",
            params={"status": req.status},
            http_status=status.HTTP_409_CONFLICT,
        )
    remaining = req.cooldown_ends_at - now_utc()
    if remaining.total_seconds() > 0:
        # 冷静期未满:不可执行但不驳回(用户可能还想用满这段时间/撤销)
        raise AppError(
            ErrorCode.CONFLICT,
            key="account.deletionCooldown",
            params={"hours": math.ceil(remaining.total_seconds() / 3600)},
            http_status=status.HTTP_409_CONFLICT,
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
        raise AppError(
            ErrorCode.CONFLICT,
            key="account.deletionLeftovers",
            params={
                "instances": len(leftovers["instances"]),
                "disks": len(leftovers["disks"]),
            },
            http_status=status.HTTP_409_CONFLICT,
            detail=leftovers,
        )
    balance = await billing_service.get_balance(session, user.id)
    if balance != 0:
        _auto_reject_deletion(
            req,
            admin_id=admin_id,
            note=f"自动驳回:余额 ¥{format(balance, 'f')} 未提现,请先经退款流程提现,到账后重新申请",
        )
        await session.commit()
        raise AppError(
            ErrorCode.CONFLICT,
            key="account.deletionBalanceRemaining",
            params={"balance": format(balance, "f")},
            http_status=status.HTTP_409_CONFLICT,
            detail={"balance": format(balance, "f")},
        )
    # 匿名化:手机号哈希化(释放唯一约束,原号码可再注册)、身份字段清空、全撤登录态。
    # 账本 balance_ledger/账单按法定义务保留,不动。
    user.phone = f"del:{user.id}:{hashlib.sha256(user.phone.encode()).hexdigest()[:12]}"
    user.id_name = None
    user.id_number = None
    user.company_name = None
    user.company_tax_id = None
    user.invoice_title = None
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
    """驳回注销申请(理由必填,不受冷静期限制)。驳回后用户可重新申请。"""
    req = await _get_deletion_for_update(session, request_id)
    if req.status != "pending":
        raise AppError(
            ErrorCode.CONFLICT,
            key="account.deletionNotPending",
            params={"status": req.status},
            http_status=status.HTTP_409_CONFLICT,
        )
    req.status = "rejected"
    req.processed_by = admin_id
    req.processed_at = now_utc()
    req.note = note
    await session.commit()
    logger.info("deletion_rejected", request_id=request_id)
    return req
