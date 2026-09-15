"""包周期预付、续费与到期巡检;中途释放不退款,首次调度失败可退还未启动订阅。"""

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
from app.core.metrics import SUBSCRIPTION_UNPAID_RUNNING
from app.core.patrol import for_each
from app.core.platform_config import get_runtime_config
from app.core.pricing import (
    MARKET_SUBSCRIPTION,
    SubscriptionQuote,
    period_delta,
    quote_subscription,
)
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

REASON_EXPIRED_STOP = "subscription_expired"
REASON_EXPIRED_FREEZE = "subscription_freeze"

_PERIOD_LABELS = {"day": "日", "week": "周", "month": "月", "year": "年"}

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
    """按当前运行时策略计算包周期报价,不落库。"""
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
    """查询幂等窗口内同 (user_id, key) 的订阅行。

    instance_id/period/period_count 全部给定时按新订阅动作校验指纹,否则仅按键查询。
    """
    if instance_id is not None and period is not None and period_count is not None:
        return await find_replay(
            session,
            Subscription,
            owner_col=Subscription.user_id,
            owner_id=user_id,
            key=key,
            window=IDEMPOTENCY_WINDOW,
            fingerprint=_fingerprint(_ACT_NEW, user_id, instance_id, period, period_count),
        )
    return await find_replay(
        session,
        Subscription,
        owner_col=Subscription.user_id,
        owner_id=user_id,
        key=key,
        window=IDEMPOTENCY_WINDOW,
    )


async def quote_of_row(
    session: AsyncSession, row: Subscription, gpu_count: int
) -> SubscriptionQuote:
    """使用订阅保存的原价与周期,按当前策略重新报价。"""
    return await quote(
        session,
        base_hourly=row.unit_price,
        gpu_count=gpu_count,
        period=row.period,
        period_count=row.period_count,
    )


async def convert(
    session: AsyncSession,
    *,
    instance: "Instance",
    period: str,
    period_count: int,
    idempotency_key: str | None,
) -> tuple[Subscription, SubscriptionQuote, bool]:
    """从当前时刻开出订阅并预扣,按 instance.price_hourly 报价;不提交。

    调用方须先结清转换前的按量费用;最新订阅为 active 时拒绝。
    """
    current = await current_for_instance(session, instance.id)
    if current is not None and current.status == STATUS_ACTIVE:
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
            return existing, await quote_of_row(session, existing, instance.gpu_count), False

    current = await current_for_instance(session, instance.id, for_update=True)
    if current is None:
        raise AppError(ErrorCode.SUBSCRIPTION_NOT_RENEWABLE, key="billing.subscriptionMissing")
    if current.status == STATUS_CANCELLED:
        raise AppError(ErrorCode.SUBSCRIPTION_NOT_RENEWABLE, key="billing.subscriptionCancelled")
    quoted = await quote(
        session,
        base_hourly=current.unit_price,
        gpu_count=instance.gpu_count,
        period=period,
        period_count=period_count,
    )
    row = _next_period_row(
        current,
        quoted,
        period=period,
        period_count=period_count,
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
    )
    current.status = STATUS_EXPIRED
    winner = await _insert_renewal(
        session, row, idempotency_key=idempotency_key, fingerprint=fingerprint
    )
    if winner is not row:
        return winner, await quote_of_row(session, winner, instance.gpu_count), False
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


def _next_period_row(
    current: Subscription,
    quoted: SubscriptionQuote,
    *,
    period: str,
    period_count: int,
    idempotency_key: str | None,
    fingerprint: str,
) -> Subscription:
    """续费新行:沿用老行的 SKU / 原价 / 自动续费开关,从 max(老到期时刻, 现在) 起算,
    串 renewed_from_id。"""
    started = max(ensure_utc(current.expires_at), now_utc())
    return Subscription(
        user_id=current.user_id,
        instance_id=current.instance_id,
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


async def _insert_renewal(
    session: AsyncSession, row: Subscription, *, idempotency_key: str | None, fingerprint: str
) -> Subscription:
    """落续费行:带幂等键时同键并发返回胜出方;无键时并发撞部分唯一索引
    (同实例至多一条 active)转 409。"""
    if idempotency_key:
        return await insert_idempotent(
            session,
            row,
            model=Subscription,
            owner_col=Subscription.user_id,
            owner_id=row.user_id,
            key=idempotency_key,
            fingerprint=fingerprint,
        )
    try:
        await insert_idempotent(
            session, row, model=Subscription, owner_col=None, owner_id=None, key=None
        )
    except IntegrityError:
        raise conflict(key="common.retryableConflict") from None
    return row


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
    return {row.instance_id: row for row in rows}


async def reserved_instance_ids(
    session: AsyncSession, instance_ids: list[int] | None = None
) -> set[int]:
    """返回 active 且未到期的订阅实例 id;instance_ids 非 None 时仅筛选给定集合。"""
    if instance_ids is not None and not instance_ids:
        return set()
    stmt = select(Subscription.instance_id).where(
        Subscription.status == STATUS_ACTIVE, Subscription.expires_at > now_utc()
    )
    if instance_ids is not None:
        stmt = stmt.where(Subscription.instance_id.in_(instance_ids))
    return set((await session.execute(stmt)).scalars().all())


async def expired_instance_ids(session: AsyncSession) -> set[int]:
    """返回有已到期 expired 订阅且不在保的实例 id。"""
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
    """更新本人最新的非 cancelled 订阅的自动续费开关;不提交。"""
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
    """锁定并作废 active 订阅,将预付原额退至指定用户钱包;不提交。

    调用方须确认实例从未运行且首次调度已失败。返回退款合计,无 active 行时返回 None。
    """
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


async def subscription_patrol(sm: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """持咨询锁执行临期预警、自动续费、到期停机与冻结,返回各动作计数。"""
    counts = {"warned": 0, "renewed": 0, "renew_failed": 0, "stopped": 0, "frozen": 0}
    async with advisory_lock(sm, LockKey.SUBSCRIPTION_PATROL) as got:
        if not got:
            return counts
        await _patrol_due(sm, counts)
        await _patrol_expired_sweep(sm, counts)
        await _refresh_unpaid_running_gauge(sm)
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

    async def handle(subscription_id: int) -> None:
        async with sm() as session:
            await _handle_due(session, subscription_id, counts)
            await session.commit()

    await for_each(
        due_ids, handle, stage="subscription_due", ident=lambda sid: {"subscription_id": sid}
    )


async def _handle_due(session: AsyncSession, subscription_id: int, counts: dict[str, int]) -> None:
    """处理临期订阅;到期续费按 instance → wallet → subscription 持锁。

    自动续费失败时先提交释放钱包锁,再进入停机结算事务。
    """
    row = (
        await session.execute(select(Subscription).where(Subscription.id == subscription_id))
    ).scalar_one_or_none()
    if row is None or row.status != STATUS_ACTIVE:
        return
    expires = ensure_utc(row.expires_at)
    now = now_utc()
    if expires > now:
        if await _warn_expiring(session, row, expires, now):
            counts["warned"] += 1
        return

    instance = await orchestrator_queries.instance_by_id(session, row.instance_id)
    await orchestrator_queries.lock_instance_for_billing(session, instance.id)
    await session.refresh(instance)
    if row.auto_renew:
        if await _try_auto_renew(session, row, instance, counts):
            return
        await session.commit()
        await session.refresh(row)
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
    """先锁钱包并刷新订阅再续费;订阅已非 active 时返回 True,余额不足时通知并返回 False。

    不透支、不提交;调用方须持实例锁。
    """
    await wallet.lock_wallet(session, row.user_id)
    await session.refresh(row)
    if row.status != STATUS_ACTIVE:
        return True
    quoted = await quote(
        session,
        base_hourly=row.unit_price,
        gpu_count=instance.gpu_count,
        period=row.period,
        period_count=row.period_count,
    )
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
    """到期处置:running → 停机;stopped → 直接冻结;其余状态不动,
    由 _patrol_expired_sweep 每轮按「已到期且不在保」重扫直到落入这两态。"""
    if instance.status == sm_def.RUNNING:
        await orchestrator_transitions.system_stop(session, instance, reason=REASON_EXPIRED_STOP)
        counts["stopped"] += 1
    elif instance.status == sm_def.STOPPED:
        await _freeze(session, instance)
        counts["frozen"] += 1
    else:
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
    """按 freeze_grace_hours 设置冻结截止时间,不提交。"""
    policies = await get_runtime_config(session)
    await orchestrator_transitions.freeze_instance(
        session,
        instance,
        now_utc() + timedelta(hours=policies.freeze_grace_hours),
        reason=REASON_EXPIRED_FREEZE,
    )


_SWEEP_STATUSES = (sm_def.RUNNING, sm_def.STOPPED)


async def _patrol_expired_sweep(
    sm: async_sessionmaker[AsyncSession], counts: dict[str, int]
) -> None:
    """逐实例独立事务处置「订阅已到期且不在保」的 running / stopped 实例:
    running → 停机,stopped → 冻结。到期时刻落在 creating/starting/stopping 的实例由本趟接手。"""
    async with sm() as session:
        expired = await expired_instance_ids(session)
        if not expired:
            return
        candidates = [
            inst
            for status in _SWEEP_STATUSES
            for inst in await orchestrator_queries.list_instances_by_status(session, status)
            if inst.id in expired
        ]

    async def expire_if_still_due(inst: "Instance") -> None:
        async with sm() as session:
            fresh = await orchestrator_queries.lock_instance(session, inst.id)
            if fresh is None or fresh.status not in _SWEEP_STATUSES:
                return
            if fresh.id in await reserved_instance_ids(session, [fresh.id]):
                return
            await _expire_instance(session, fresh, counts)
            await session.commit()

    await for_each(
        candidates,
        expire_if_still_due,
        stage="subscription_sweep",
        ident=lambda inst: {"instance_id": inst.id},
    )


_UNPAID_GAUGE_STATUSES = (sm_def.CREATING, sm_def.STARTING, sm_def.RUNNING)


async def _refresh_unpaid_running_gauge(sm: async_sessionmaker[AsyncSession]) -> None:
    """刷新「包周期实例活跃但无在保订阅」计数;>0 即到期链路有漏网。"""
    async with sm() as session:
        active = [
            inst
            for status in _UNPAID_GAUGE_STATUSES
            for inst in await orchestrator_queries.list_instances_by_status(session, status)
            if inst.market == MARKET_SUBSCRIPTION
        ]
        reserved = await reserved_instance_ids(session, [inst.id for inst in active])
    unpaid = sum(1 for inst in active if inst.id not in reserved)
    SUBSCRIPTION_UNPAID_RUNNING.set(unpaid)
    if unpaid:
        logger.error("subscription_unpaid_running", count=unpaid)
