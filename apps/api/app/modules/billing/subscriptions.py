"""包周期(预付)订阅:下单预扣、续费、到期巡检。

包周期不进 `bills_hourly`,只在结算候选里被跳过(orchestrator/queries.billing_candidates)。
预付语义:中途释放不退款(订阅转 cancelled);到期不自动转按量,到期即停机;余额为零不停机——
停机判据、燃烧率、在途预留、冻结链四处都排除包周期实例。
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError, ErrorCode, conflict
from app.core.idempotency import (
    IDEMPOTENCY_WINDOW,
    find_replay,
    insert_idempotent,
    request_fingerprint,
)
from app.core.locks import LockKey, advisory_lock
from app.core.logging import get_logger
from app.core.metrics import PATROL_FAILED_TOTAL
from app.core.platform_config import get_runtime_config
from app.core.pricing import SubscriptionQuote, period_delta, quote_subscription
from app.core.timeutil import ensure_utc, now_utc
from app.modules.billing import wallet
from app.modules.billing.models import Subscription
from app.modules.notify import service as notify_service
from app.modules.orchestrator import (
    queries as orchestrator_queries,
    statemachine as sm_def,
    transitions as orchestrator_transitions,
)

if TYPE_CHECKING:
    from app.modules.orchestrator.models import Instance

logger = get_logger(__name__)

STATUS_ACTIVE = "active"
STATUS_EXPIRED = "expired"
STATUS_CANCELLED = "cancelled"

# 到期未续费的处置理由(instance_events.reason),与欠费链路的 arrears_* 分开命名
REASON_EXPIRED_STOP = "subscription_expired"
REASON_EXPIRED_FREEZE = "subscription_freeze"

_PERIOD_LABELS = {"day": "日", "week": "周", "month": "月", "year": "年"}

# 幂等指纹的动作名:转换/首单与续费共用 UNIQUE(user_id, idempotency_key),动作名进指纹
_ACT_NEW = "subscription:new"
_ACT_RENEW = "subscription:renew"


def period_label(period: str) -> str:
    return _PERIOD_LABELS.get(period, period)


def _fingerprint(action: str, user_id: int, instance_id: int, period: str, count: int) -> str:
    """订阅单的请求指纹:动作 + 归属 + 目标实例 + 周期。"""
    return request_fingerprint(action, user_id, instance_id, period, count)


async def quote(
    session: AsyncSession,
    *,
    base_hourly: Decimal,
    gpu_count: int,
    period: str,
    period_count: int,
) -> SubscriptionQuote:
    """报价(不落库)。市场页、创建预估、续费 modal 都经这里。"""
    policies = await get_runtime_config(session)
    return quote_subscription(
        base_hourly,
        gpu_count=gpu_count,
        period=period,
        period_count=period_count,
        policies=policies,
    )


async def charge_new(
    session: AsyncSession,
    *,
    user_id: int,
    instance_id: int,
    instance_name: str,
    sku_id: int,
    base_hourly: Decimal,
    gpu_count: int,
    period: str,
    period_count: int,
    idempotency_key: str | None,
) -> tuple[Subscription, SubscriptionQuote]:
    """下单预扣:写 subscriptions 行 + 扣款 + 流水。不 commit,由调用方并入建实例事务。
    扣款 allow_negative=False(先付后用);余额不够抛 INSUFFICIENT_BALANCE。
    """
    quoted = await quote(
        session,
        base_hourly=base_hourly,
        gpu_count=gpu_count,
        period=period,
        period_count=period_count,
    )
    started = now_utc()
    row = Subscription(
        user_id=user_id,
        instance_id=instance_id,
        sku_id=sku_id,
        period=period,
        period_count=period_count,
        unit_price=quoted.base_hourly,
        amount_paid=quoted.amount,
        started_at=started,
        expires_at=started + period_delta(period, period_count),
        status=STATUS_ACTIVE,
        idempotency_key=idempotency_key,
        request_fingerprint=_fingerprint(_ACT_NEW, user_id, instance_id, period, period_count),
    )
    session.add(row)
    await session.flush()
    await wallet.debit(
        session,
        user_id,
        quoted.amount,
        type_="consume",
        ref_type="subscription",
        ref_id=str(row.id),
        remark=f"{instance_name} 包{period_label(period)}×{period_count}",
        allow_negative=False,
        # 先付后用:allow_frozen=False
    )
    return row, quoted


async def list_expiring_active(
    session: AsyncSession, user_id: int, *, within_days: int
) -> list[Subscription]:
    """临期 active 订阅:expires_at ≤ now+within_days,按到期时刻升序,上限 50。"""
    horizon = now_utc() + timedelta(days=within_days)
    return list(
        (
            await session.execute(
                select(Subscription)
                .where(
                    Subscription.user_id == user_id,
                    Subscription.status == STATUS_ACTIVE,
                    Subscription.expires_at <= horizon,
                )
                .order_by(Subscription.expires_at)
                .limit(50)
            )
        ).scalars()
    )


async def find_replay_row(
    session: AsyncSession,
    *,
    user_id: int,
    key: str,
    instance_id: int | None = None,
    period: str | None = None,
    period_count: int | None = None,
) -> Subscription | None:
    """幂等窗口内同 (user_id, key) 的订阅行。转换/续费的第一步先问它。
    给全 instance_id/period/period_count 时做异参检测:同键不同实例或周期即 409;
    三个参数缺一即退化为纯按键重放。
    """
    fingerprint = (
        _fingerprint(_ACT_NEW, user_id, instance_id, period, period_count)
        if instance_id is not None and period is not None and period_count is not None
        else None
    )
    if fingerprint is None:
        return await find_replay(
            session,
            Subscription,
            owner_col=Subscription.user_id,
            owner_id=user_id,
            key=key,
            window=IDEMPOTENCY_WINDOW,
        )
    return await find_replay(
        session,
        Subscription,
        owner_col=Subscription.user_id,
        owner_id=user_id,
        key=key,
        window=IDEMPOTENCY_WINDOW,
        fingerprint=fingerprint,
    )


async def quote_of_row(
    session: AsyncSession, row: Subscription, gpu_count: int
) -> SubscriptionQuote:
    """按已落库的订阅行反算报价(幂等重放的响应体与首次一致)。"""
    return await _quote_of(session, row, gpu_count)


async def convert(
    session: AsyncSession,
    *,
    instance: "Instance",
    period: str,
    period_count: int,
    idempotency_key: str | None,
) -> tuple[Subscription, SubscriptionQuote, bool]:
    """按量实例转包周期:开出这台实例的第一张订阅单。不 commit。

    从现在起算(转换前那段按量时间由调用方先结清:orchestrator.subscribe_instance →
    settle_on_demand_up_to)。报价基准是 `instance.price_hourly`(SKU 原价快照),不是 SKU 现价。
    """
    current = await current_for_instance(session, instance.id)
    if current is not None and current.status == STATUS_ACTIVE:
        # 已在保不可再转
        raise AppError(
            ErrorCode.SUBSCRIPTION_NOT_RENEWABLE, key="billing.subscriptionAlreadyActive"
        )
    row, quoted = await charge_new(
        session,
        user_id=instance.user_id,
        instance_id=instance.id,
        instance_name=instance.name,
        sku_id=instance.sku_id,
        base_hourly=instance.price_hourly,
        gpu_count=instance.gpu_count,
        period=period,
        period_count=period_count,
        idempotency_key=idempotency_key,
    )
    logger.info(
        "subscription_converted",
        subscription_id=row.id,
        instance_id=instance.id,
        period=period,
        count=period_count,
        amount=str(quoted.amount),
    )
    return row, quoted, True


async def renew(
    session: AsyncSession,
    *,
    instance: "Instance",
    period: str,
    period_count: int,
    idempotency_key: str | None,
    actor: str = "user",
) -> tuple[Subscription, SubscriptionQuote, bool]:
    """续费:老行转 expired,新开一行并串 renewed_from_id。不 commit。
    返回 (新订阅, 报价, created);created=False = 幂等重放。

    新周期从 max(老周期到期时刻, 现在) 起算。定价基准是 `subscriptions.unit_price`(SKU 原价快照)。
    调用前必须先持钱包行锁(lock_wallet),老订阅行在锁内 FOR UPDATE 重读;锁序 wallet → subscriptions。
    """
    fingerprint = _fingerprint(_ACT_RENEW, instance.user_id, instance.id, period, period_count)
    if idempotency_key:
        existing = await find_replay(
            session,
            Subscription,
            owner_col=Subscription.user_id,
            owner_id=instance.user_id,
            key=idempotency_key,
            window=IDEMPOTENCY_WINDOW,
            fingerprint=fingerprint,
        )
        if existing is not None:
            return existing, await _quote_of(session, existing, instance.gpu_count), False

    current = await current_for_instance(session, instance.id, for_update=True)
    if current is None:
        raise AppError(ErrorCode.SUBSCRIPTION_NOT_RENEWABLE, key="billing.subscriptionMissing")
    if current.status == STATUS_CANCELLED:
        # 与「压根没买过」分开报
        raise AppError(ErrorCode.SUBSCRIPTION_NOT_RENEWABLE, key="billing.subscriptionCancelled")
    quoted = await quote(
        session,
        base_hourly=current.unit_price,
        gpu_count=instance.gpu_count,
        period=period,
        period_count=period_count,
    )
    started = max(ensure_utc(current.expires_at), now_utc())
    row = Subscription(
        user_id=instance.user_id,
        instance_id=instance.id,
        sku_id=current.sku_id,
        period=period,
        period_count=period_count,
        unit_price=current.unit_price,
        amount_paid=quoted.amount,
        started_at=started,
        expires_at=started + period_delta(period, period_count),
        status=STATUS_ACTIVE,
        auto_renew=current.auto_renew,
        renewed_from_id=current.id,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
    )
    current.status = STATUS_EXPIRED
    if idempotency_key:
        result = await insert_idempotent(
            session,
            row,
            model=Subscription,
            owner_col=Subscription.user_id,
            owner_id=instance.user_id,
            key=idempotency_key,
            fingerprint=fingerprint,
        )
        if result is not row:
            # 并发同幂等键由 UNIQUE(user_id, idempotency_key) 兜住,胜出方按重放返回;
            # insert_idempotent 内部 rollback 同时撤掉对 current 的改动
            return result, await _quote_of(session, result, instance.gpu_count), False
    else:
        try:
            await insert_idempotent(
                session, row, model=Subscription, owner_col=None, owner_id=None, key=None
            )
        except IntegrityError:
            # 理论不可达(钱包锁 + 行锁已串行化):部分唯一索引 uq_subscriptions_active_instance 兜底
            raise conflict(key="common.retryableConflict") from None
    await wallet.debit(
        session,
        instance.user_id,
        quoted.amount,
        type_="consume",
        ref_type="subscription",
        ref_id=str(row.id),
        remark=f"{instance.name} 续费 包{period_label(period)}×{period_count}",
        allow_negative=False,
    )
    logger.info(
        "subscription_renewed",
        subscription_id=row.id,
        instance_id=instance.id,
        period=period,
        count=period_count,
        amount=str(quoted.amount),
        actor=actor,
    )
    return row, quoted, True


async def _quote_of(session: AsyncSession, row: Subscription, gpu_count: int) -> SubscriptionQuote:
    """按已落库的订阅行反算报价(幂等重放的响应体与首次一致)。"""
    return await quote(
        session,
        base_hourly=row.unit_price,
        gpu_count=gpu_count,
        period=row.period,
        period_count=row.period_count,
    )


# ---------- 查询 ----------


async def current_for_instance(
    session: AsyncSession, instance_id: int, *, for_update: bool = False
) -> Subscription | None:
    """该实例当前生效(或最后一期)的订阅行:取 id 最大的一行。
    for_update=True 给续费路径,调用前必须先持钱包行锁(锁序 wallet → subscriptions)。
    """
    stmt = (
        select(Subscription)
        .where(Subscription.instance_id == instance_id)
        .order_by(Subscription.id.desc())
        .limit(1)
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def latest_by_instance(
    session: AsyncSession, instance_ids: list[int]
) -> dict[int, Subscription]:
    """批量版 current_for_instance。"""
    if not instance_ids:
        return {}
    rows = (
        (
            await session.execute(
                select(Subscription)
                .where(Subscription.instance_id.in_(instance_ids))
                .order_by(Subscription.id)
            )
        )
        .scalars()
        .all()
    )
    # 按 id 升序遍历,后写覆盖先写 → 每个实例留 id 最大的那行
    return {row.instance_id: row for row in rows}


async def reserved_instance_ids(
    session: AsyncSession, instance_ids: list[int] | None = None
) -> set[int]:
    """仍在保(active 且未到期)的包周期实例 id;给了 instance_ids 就只在其中筛。
    软准入据此把「已停机但周期未满」的实例计为占用。
    """
    if instance_ids is not None and not instance_ids:
        return set()
    stmt = select(Subscription.instance_id).where(
        Subscription.status == STATUS_ACTIVE, Subscription.expires_at > now_utc()
    )
    if instance_ids is not None:
        stmt = stmt.where(Subscription.instance_id.in_(instance_ids))
    return set((await session.execute(stmt)).scalars().all())


async def expired_instance_ids(session: AsyncSession) -> set[int]:
    """最后一期已到期(且未被释放)的包周期实例 id(到期冻结链路候选)。"""
    expired = set(
        (
            await session.execute(
                select(Subscription.instance_id).where(
                    Subscription.status == STATUS_EXPIRED,
                    Subscription.expires_at <= now_utc(),
                )
            )
        )
        .scalars()
        .all()
    )
    # 减去在保集合(续过费的实例链上同时有 expired 老行与 active 新行)
    return expired - await reserved_instance_ids(session)


async def assert_active(session: AsyncSession, instance_id: int) -> Subscription:
    """包周期实例的开机门禁:周期内才让开机。行缺失也判过期(fail-closed)。"""
    row = await current_for_instance(session, instance_id)
    if row is None or row.status != STATUS_ACTIVE or ensure_utc(row.expires_at) <= now_utc():
        raise AppError(
            ErrorCode.SUBSCRIPTION_EXPIRED,
            key="billing.subscriptionExpired",
            http_status=409,
        )
    return row


async def set_auto_renew(
    session: AsyncSession, *, user_id: int, instance_id: int, enabled: bool
) -> Subscription:
    """开关自动续费。**不 commit**。"""
    row = await current_for_instance(session, instance_id)
    if row is None or row.user_id != user_id:
        raise AppError(ErrorCode.SUBSCRIPTION_NOT_RENEWABLE, key="billing.subscriptionMissing")
    if row.status == STATUS_CANCELLED:
        raise AppError(ErrorCode.SUBSCRIPTION_NOT_RENEWABLE, key="billing.subscriptionCancelled")
    row.auto_renew = enabled
    return row


async def cancel_for_instance(session: AsyncSession, instance_id: int) -> None:
    """实例进入 releasing 时作废订阅(预付不退款)。不 commit。只动 active 行,expired 历史行不动。"""
    for row in (
        (
            await session.execute(
                select(Subscription).where(
                    Subscription.instance_id == instance_id,
                    Subscription.status == STATUS_ACTIVE,
                )
            )
        )
        .scalars()
        .all()
    ):
        row.status = STATUS_CANCELLED


async def refund_unstarted(session: AsyncSession, instance_id: int, user_id: int) -> Decimal | None:
    """实例从未运行即 failed(首次 creating 调度超时):作废 active 订阅并把预付原额退回余额
    (ledger type=refund / ref_type=subscription)。不 commit。返回退回合计;无 active 行返回 None。"""
    rows = (
        (
            await session.execute(
                select(Subscription)
                .where(
                    Subscription.instance_id == instance_id,
                    Subscription.status == STATUS_ACTIVE,
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    total = Decimal("0.00")
    for row in rows:
        row.status = STATUS_CANCELLED
        if row.amount_paid > 0:
            await wallet.credit(
                session,
                user_id,
                row.amount_paid,
                type_="refund",
                ref_type="subscription",
                ref_id=str(row.id),
                remark="实例调度超时未启动,包周期预付原额退回",
            )
            total += row.amount_paid
    return total


# ---------- 到期巡检 ----------


async def subscription_patrol(sm: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """包周期到期链路(每 30 分钟):预警 → 自动续费 → 到期停机 → 冻结(写 frozen_deadline)。
    frozen 到期回收由 balance_patrol 的 `_patrol_frozen_and_arrears_stopped` 统一做。
    """
    counts = {"warned": 0, "renewed": 0, "renew_failed": 0, "stopped": 0, "frozen": 0}
    async with advisory_lock(sm, LockKey.SUBSCRIPTION_PATROL) as got:
        if not got:
            return counts
        await _patrol_due(sm, counts)
        await _patrol_freeze_expired(sm, counts)
    logger.info("subscription_patrol_done", **counts)
    return counts


async def _patrol_due(sm: async_sessionmaker[AsyncSession], counts: dict[str, int]) -> None:
    """临期预警 + 到期处置。逐条独立事务。"""
    async with sm() as session:
        policies = await get_runtime_config(session)
        horizon = now_utc() + timedelta(days=policies.period_expire_warn_days)
        due_ids = list(
            (
                await session.execute(
                    select(Subscription.id).where(
                        Subscription.status == STATUS_ACTIVE,
                        Subscription.expires_at <= horizon,
                    )
                )
            )
            .scalars()
            .all()
        )
    for subscription_id in due_ids:
        try:
            async with sm() as session:
                await _handle_due(session, subscription_id, counts)
                await session.commit()
        except Exception:
            PATROL_FAILED_TOTAL.labels(stage="subscription_due").inc()
            logger.exception("subscription_patrol_failed", subscription_id=subscription_id)


async def _handle_due(session: AsyncSession, subscription_id: int, counts: dict[str, int]) -> None:
    row = (
        await session.execute(select(Subscription).where(Subscription.id == subscription_id))
    ).scalar_one_or_none()
    # 取快照到现在之间,用户可能已自己续费或释放
    if row is None or row.status != STATUS_ACTIVE:
        return
    expires = ensure_utc(row.expires_at)
    now = now_utc()
    if expires > now:
        if await _warn_expiring(session, row, expires, now):
            counts["warned"] += 1
        return

    instance = await orchestrator_queries.instance_by_id(session, row.instance_id)
    # 锁序 instance → wallet → subscription(与停机/结算链路 instance → bill → wallet 一致)。
    # 锁后 refresh:transition 的乐观锁要新鲜 version
    await orchestrator_queries.lock_instance_for_billing(session, instance.id)
    await session.refresh(instance)
    if row.auto_renew:
        if await _try_auto_renew(session, row, instance, counts):
            return
        # 续费失败:先提交释放钱包锁,停机链路在下一事务按 instance → bill → wallet 重新持锁
        await session.commit()
        await session.refresh(row)  # 等锁/提交期间用户可能已手动续费
        if row.status != STATUS_ACTIVE:
            return
    row.status = STATUS_EXPIRED
    await _expire_instance(session, instance, counts)


async def _warn_expiring(
    session: AsyncSession, row: Subscription, expires: datetime, now: datetime
) -> bool:
    """到期预警。warned_for_expiry 存「已预警到哪个到期时刻」。"""
    if row.warned_for_expiry is not None and ensure_utc(row.warned_for_expiry) == expires:
        return False
    row.warned_for_expiry = expires
    days = max(0, round((expires - now).total_seconds() / 86400))
    # 深链目标(实例 uuid)
    instance = await orchestrator_queries.instance_by_id(session, row.instance_id)
    await notify_service.send_subscription_notice(
        session,
        row.user_id,
        action="expiring",
        detail=(
            f"包{period_label(row.period)}将于 {expires:%Y-%m-%d %H:%M} UTC 到期"
            f"(剩 {days} 天),到期后自动停机。请及时续费。"
        ),
        dedup_suffix=str(row.id),
        target_id=instance.uuid,
    )
    return True


async def _try_auto_renew(
    session: AsyncSession, row: Subscription, instance: "Instance", counts: dict[str, int]
) -> bool:
    """自动续费。可用余额不够返回 False 走到期停机链路,绝不透支;先算价再比余额。
    与手动续费同一锁序:先 lock_wallet 再动订阅行;等锁期间可能已被手动续费,refresh 复核后放弃。
    """
    await wallet.lock_wallet(session, row.user_id)
    await session.refresh(row)
    if row.status != STATUS_ACTIVE:
        # 等钱包锁期间已被并发续费:视为已处理
        return True
    quoted = await quote(
        session,
        base_hourly=row.unit_price,
        gpu_count=instance.gpu_count,
        period=row.period,
        period_count=row.period_count,
    )
    # 预检口径与 debit 的冻结闸同线:可用余额(balance - frozen)不够即落 renew_failed 并通知
    if await wallet.get_available_balance(session, row.user_id) < quoted.amount:
        counts["renew_failed"] += 1
        await notify_service.send_subscription_notice(
            session,
            row.user_id,
            action="renew_failed",
            detail="余额不足,自动续费失败,实例将停机。充值后可手动续费。",
            dedup_suffix=str(row.id),
            target_id=instance.uuid,
        )
        return False
    await renew(
        session,
        instance=instance,
        period=row.period,
        period_count=row.period_count,
        idempotency_key=None,
        actor="system",
    )
    counts["renewed"] += 1
    await notify_service.send_subscription_notice(
        session,
        row.user_id,
        action="renewed",
        detail=(
            f"已自动续费 包{period_label(row.period)}×{row.period_count},扣款 ¥{quoted.amount}。"
        ),
        dedup_suffix=str(row.id),
        target_id=instance.uuid,
    )
    return True


async def _expire_instance(
    session: AsyncSession, instance: "Instance", counts: dict[str, int]
) -> None:
    """到期处置:running → 停机(停稳后由 _patrol_freeze_expired 冻结);stopped → 直接冻结;
    其余状态本轮不动。"""
    if instance.status == sm_def.RUNNING:
        await orchestrator_transitions.system_stop(session, instance, reason=REASON_EXPIRED_STOP)
        counts["stopped"] += 1
    elif instance.status == sm_def.STOPPED:
        await _freeze(session, instance)
        counts["frozen"] += 1
    else:
        # creating/starting/stopping/frozen/releasing:本轮动不了,下一轮接手。订阅行已置 expired
        return
    await notify_service.send_subscription_notice(
        session,
        instance.user_id,
        action="expired",
        detail="包周期已到期,实例已停机;72 小时内未续费将回收实例盘(数据盘不受影响)。",
        dedup_suffix=str(instance.id),
        target_id=instance.uuid,
    )


async def _freeze(session: AsyncSession, instance: "Instance") -> None:
    """冻结窗口复用 `freeze_grace_hours`(欠费同款)。"""
    policies = await get_runtime_config(session)
    await orchestrator_transitions.freeze_instance(
        session,
        instance,
        now_utc() + timedelta(hours=policies.freeze_grace_hours),
        reason=REASON_EXPIRED_FREEZE,
    )


async def _patrol_freeze_expired(
    sm: async_sessionmaker[AsyncSession], counts: dict[str, int]
) -> None:
    """已到期且已停稳的包周期实例 → 冻结(起回收倒计时,时长见 _freeze)。
    单独一趟:停机是异步的,到期那一刻实例还在 stopping。
    """
    async with sm() as session:
        expired = await expired_instance_ids(session)
        if not expired:
            return
        candidates = [
            inst
            for inst in await orchestrator_queries.list_instances_by_status(session, "stopped")
            if inst.id in expired
        ]
    for inst in candidates:
        try:
            async with sm() as session:
                fresh = await orchestrator_queries.instance_by_id(session, inst.id)
                if fresh.status != "stopped":
                    continue
                await _freeze(session, fresh)
                await session.commit()
                counts["frozen"] += 1
        except Exception:
            PATROL_FAILED_TOTAL.labels(stage="subscription_freeze").inc()
            logger.exception("subscription_freeze_failed", instance_id=inst.id)
