"""余额巡检(5 分钟):预警 → 欠费停机 → 冻结 72h → 到期回收 → 充值解冻。

每步落事件与通知。数据盘独立宽限。全链路判据一律是可用余额(balance − frozen,
`wallet.available_of`),与 `wallet.assert_can_afford` 同口径。
"""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.locks import LockKey, advisory_lock
from app.core.logging import get_logger
from app.core.metrics import PATROL_FAILED_TOTAL
from app.core.money import as_amount, hourly_cost, money_str
from app.core.policies import get_effective_policies
from app.core.pricing import MARKET_SUBSCRIPTION
from app.core.timeutil import hour_floor, now_utc
from app.modules.account import service as account_service
from app.modules.billing import wallet
from app.modules.billing.models import BillHourly
from app.modules.billing.settlement import (
    bill_amount,
    get_watermark,
    running_seconds_in_window,
)
from app.modules.notify import service as notify_service

logger = get_logger(__name__)


async def balance_patrol(sm: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    counts = {
        "warned": 0,
        "stopped": 0,
        "frozen": 0,
        "reclaimed": 0,
        "unfrozen": 0,
        "disks": 0,
    }
    async with advisory_lock(sm, LockKey.BALANCE_PATROL) as got:
        if not got:
            return counts
        await _patrol_frozen_tenants(sm, counts)
        await _patrol_running(sm, counts)
        await _patrol_frozen_and_arrears_stopped(sm, counts)
        await _patrol_disks(sm, counts)
    # 无条件打 done:counts 全 0 也留完成痕迹
    logger.info("balance_patrol_done", **counts)
    return counts


async def _patrol_frozen_tenants(
    sm: async_sessionmaker[AsyncSession], counts: dict[str, int]
) -> None:
    """被冻结账号仍在跑的实例 → 停机(兜冻结时还在 creating/starting、随后收敛到 running 的实例)。"""
    from app.modules.orchestrator import service as orchestrator_service

    async with sm() as session:
        frozen_user_ids = await account_service.frozen_user_ids(session)
    if not frozen_user_ids:
        return
    for user_id in frozen_user_ids:
        try:
            async with sm() as session:
                stopped = await orchestrator_service.stop_all_for_user(
                    session, user_id, reason="tenant_frozen"
                )
                await session.commit()
                counts["stopped"] += stopped
        except Exception:
            PATROL_FAILED_TOTAL.labels(stage="frozen_tenant").inc()
            logger.exception("patrol_frozen_tenant_failed", user_id=user_id)


async def _unsettled_burn(
    session: AsyncSession, inst, now: datetime, settled_through: datetime | None
) -> Decimal:
    """该实例「已跑未出账」的实时估算消耗(2 位小数),只用于停机/预警判据,永不入账。

    窗口下界取 min(当前自然小时, 水位线+1h);与结算同口径:事件重建 running 秒数 − 已出账秒数。
    """
    from app.modules.orchestrator import service as orchestrator_service

    h0 = hour_floor(now)
    start = h0 if settled_through is None else min(h0, settled_through + timedelta(hours=1))
    events = await orchestrator_service.billing_events_before(session, inst.id, now)
    # 巡检估算不截断失联宽限(多估口径)
    seconds = running_seconds_in_window([(ts, f, t) for ts, f, t, _m in events], start, now)
    billed = (
        await session.execute(
            select(func.coalesce(func.sum(BillHourly.seconds_used), 0)).where(
                BillHourly.instance_id == inst.id, BillHourly.hour_start >= start
            )
        )
    ).scalar_one()
    unsettled_seconds = max(0, seconds - billed)
    # 多小时估算口径:上限 31 天,越界报错不截断
    return bill_amount(
        inst.price_hourly, inst.gpu_count, unsettled_seconds, max_seconds=31 * 24 * 3600
    )


async def _patrol_running(sm: async_sessionmaker[AsyncSession], counts: dict[str, int]) -> None:
    from app.modules.orchestrator import service as orchestrator_service

    async with sm() as session:
        by_user = await orchestrator_service.list_running_instances_by_user(session)
        thresholds = await account_service.get_warn_thresholds(session, list(by_user))
        settled_through = await get_watermark(session, "hourly")

    for user_id, all_instances in by_user.items():
        # 包周期实例不参与燃烧率与欠费停机。三处配套过滤之一
        # (另两处:wallet.assert_can_afford、billing.edge_listener)
        instances = [i for i in all_instances if i.market != MARKET_SUBSCRIPTION]
        if not instances:
            continue
        try:
            async with sm() as session:
                available = await wallet.get_available_balance(session, user_id)
                burn_per_hour = sum(
                    (hourly_cost(i.price_hourly, i.gpu_count) for i in instances),
                    Decimal("0.00"),
                )
                # 停机判据:可用余额 − 未结算消耗 ≤ 0
                now = now_utc()
                unsettled = Decimal("0.00")
                for inst in instances:
                    unsettled += await _unsettled_burn(session, inst, now, settled_through)
                effective = as_amount(available - unsettled)
                if effective <= 0:
                    # 锁内二次读(credit 与本锁互斥),同样走可用口径
                    locked = await wallet.lock_wallet(session, user_id)
                    effective = as_amount(wallet.available_of(locked) - unsettled)
                if effective <= 0:
                    for inst in instances:
                        fresh = await orchestrator_service.get_instance(session, user_id, inst.uuid)
                        if fresh.status == orchestrator_service.RUNNING:
                            await orchestrator_service.system_stop(
                                session, fresh, reason="arrears_stop"
                            )
                            counts["stopped"] += 1
                    await notify_service.send_arrears_notice(
                        session,
                        user_id,
                        action="auto_stop",
                        detail="余额耗尽,实例已自动关机",
                    )
                    await session.commit()
                elif burn_per_hour > 0:
                    est_hours = float(effective / burn_per_hour)
                    # 阈值存 users.low_balance_warn_hours(NOT NULL),每个 user_id 必有阈值行
                    if est_hours < thresholds[user_id]:
                        await notify_service.send_low_balance_warning(
                            session, user_id, est_hours=est_hours, balance=money_str(available)
                        )
                        counts["warned"] += 1
        except Exception:
            PATROL_FAILED_TOTAL.labels(stage="running").inc()
            logger.exception("patrol_running_failed", user_id=user_id)


async def _patrol_frozen_and_arrears_stopped(
    sm: async_sessionmaker[AsyncSession], counts: dict[str, int]
) -> None:
    from app.modules.orchestrator import service as orchestrator_service

    async with sm() as policy_session:
        policies = await get_effective_policies(policy_session)
    now = now_utc()

    async with sm() as session:
        stopped = await orchestrator_service.list_instances_by_status(
            session, orchestrator_service.STOPPED
        )
        frozen = await orchestrator_service.list_instances_by_status(
            session, orchestrator_service.FROZEN
        )

    # 欠费用户的 stopped 实例 → 冻结。包周期的冻结由 subscriptions.subscription_patrol
    # 写 frozen_deadline,回收归下面统一做
    for inst in (i for i in stopped if i.market != MARKET_SUBSCRIPTION):
        try:
            async with sm() as session:
                available = await wallet.get_available_balance(session, inst.user_id)
                if available > 0:
                    continue
                fresh = await orchestrator_service.get_instance(session, inst.user_id, inst.uuid)
                if fresh.status != orchestrator_service.STOPPED:
                    continue
                deadline = now + timedelta(hours=policies.freeze_grace_hours)
                await orchestrator_service.freeze_instance(session, fresh, deadline)
                await notify_service.send_arrears_notice(
                    session,
                    inst.user_id,
                    action="freeze",
                    detail=f"欠费冻结,{policies.freeze_grace_hours} 小时后将回收实例盘",
                )
                await session.commit()
                counts["frozen"] += 1
        except Exception:
            PATROL_FAILED_TOTAL.labels(stage="freeze").inc()
            logger.exception("patrol_freeze_failed", instance_id=inst.id)

    # frozen:充值 → 解冻;到期 → 回收
    for inst in frozen:
        try:
            async with sm() as session:
                fresh = await orchestrator_service.get_instance(session, inst.user_id, inst.uuid)
                if fresh.status != orchestrator_service.FROZEN:
                    continue
                # 解冻条件按购买模式分:按量看可用余额,包周期看续费
                available = await wallet.get_available_balance(session, inst.user_id)
                if available > 0 and fresh.market != MARKET_SUBSCRIPTION:
                    await orchestrator_service.unfreeze_instance(session, fresh)
                    counts["unfrozen"] += 1
                elif fresh.frozen_deadline is not None and fresh.frozen_deadline <= now:
                    await orchestrator_service.reclaim_frozen(session, fresh)
                    await notify_service.send_arrears_notice(
                        session,
                        inst.user_id,
                        action="reclaim",
                        detail="冻结期满,实例已回收(实例盘清除,数据盘保留)",
                    )
                    counts["reclaimed"] += 1
                await session.commit()
        except Exception:
            PATROL_FAILED_TOTAL.labels(stage="frozen").inc()
            logger.exception("patrol_frozen_failed", instance_id=inst.id)


async def _patrol_disks(sm: async_sessionmaker[AsyncSession], counts: dict[str, int]) -> None:
    """数据盘欠费链路:欠费 → grace(只读,disk_grace_days)→ frozen(disk_frozen_days)→ 清除;
    回款即恢复。
    巡检集合见 disks.list_arrears_chain_user_ids。
    """
    from app.modules.orchestrator import service as orchestrator_service

    async with sm() as session:
        user_ids = await orchestrator_service.arrears_chain_disk_user_ids(session)
    for user_id in user_ids:
        try:
            async with sm() as session:
                available = await wallet.get_available_balance(session, user_id)
                changed = await orchestrator_service.disks_arrears_transition(
                    session, user_id, available <= 0
                )
                await session.commit()
                counts["disks"] += changed
        except Exception:
            PATROL_FAILED_TOTAL.labels(stage="disks").inc()
            logger.exception("patrol_disks_failed", user_id=user_id)
