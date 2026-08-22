"""余额巡检(5 分钟):预警 → 欠费停机 → 冻结 72h → 到期回收 → 充值解冻。

每步落事件与通知。数据盘独立宽限,不随实例回收。
"""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.locks import LockKey, try_advisory_lock
from app.core.logging import get_logger
from app.core.money import as_amount
from app.core.policies import get_effective_policies
from app.core.timeutil import now_utc
from app.modules.account import service as account_service
from app.modules.billing import wallet
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
    async with (
        sm() as lock_session,
        try_advisory_lock(lock_session, LockKey.BALANCE_PATROL) as got,
    ):
        if not got:
            return counts
        await _patrol_frozen_tenants(sm, counts)
        await _patrol_running(sm, counts)
        await _patrol_frozen_and_arrears_stopped(sm, counts)
        await _patrol_disks(sm, counts)
    if any(counts.values()):
        logger.info("balance_patrol_done", **counts)
    return counts


async def _patrol_frozen_tenants(
    sm: async_sessionmaker[AsyncSession], counts: dict[str, int]
) -> None:
    """被冻结账号仍在跑的实例 → 停机。

    兜的是冻结那一刻还在 creating/starting 的实例:状态机不允许它们直接进 stopping,
    收敛到 running 后须再停一次。
    """
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
            logger.exception("patrol_frozen_tenant_failed", user_id=user_id)


async def _patrol_running(sm: async_sessionmaker[AsyncSession], counts: dict[str, int]) -> None:
    from app.modules.orchestrator import service as orchestrator_service

    async with sm() as session:
        by_user = await orchestrator_service.list_running_instances_by_user(session)
        thresholds = await account_service.get_warn_thresholds(session, list(by_user))

    for user_id, instances in by_user.items():
        try:
            async with sm() as session:
                balance = await wallet.get_balance(session, user_id)
                burn_per_hour = sum(
                    (as_amount(i.price_hourly * i.gpu_count) for i in instances),
                    Decimal("0.00"),
                )
                if balance <= 0:
                    for inst in instances:
                        fresh = await orchestrator_service.get_instance(session, user_id, inst.uuid)
                        if fresh.status == "running":
                            await orchestrator_service.arrears_stop(session, fresh)
                            counts["stopped"] += 1
                    await notify_service.send_arrears_notice(
                        session,
                        user_id,
                        action="auto_stop",
                        detail="余额耗尽,实例已自动关机",
                    )
                    await session.commit()
                elif burn_per_hour > 0:
                    est_hours = float(balance / burn_per_hour)
                    if est_hours < thresholds.get(user_id, 24):
                        await notify_service.send_low_balance_warning(
                            session, user_id, est_hours=est_hours, balance=format(balance, "f")
                        )
                        counts["warned"] += 1
        except Exception:
            logger.exception("patrol_running_failed", user_id=user_id)


async def _patrol_frozen_and_arrears_stopped(
    sm: async_sessionmaker[AsyncSession], counts: dict[str, int]
) -> None:
    from app.modules.orchestrator import service as orchestrator_service

    async with sm() as policy_session:
        policies = await get_effective_policies(policy_session)
    now = now_utc()

    async with sm() as session:
        stopped = await orchestrator_service.list_instances_by_status(session, "stopped")
        frozen = await orchestrator_service.list_instances_by_status(session, "frozen")

    # 欠费用户的 stopped 实例 → 冻结(72h 倒计时)
    for inst in stopped:
        try:
            async with sm() as session:
                balance = await wallet.get_balance(session, inst.user_id)
                if balance > 0:
                    continue
                fresh = await orchestrator_service.get_instance(session, inst.user_id, inst.uuid)
                if fresh.status != "stopped":
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
            logger.exception("patrol_freeze_failed", instance_id=inst.id)

    # frozen:充值 → 解冻;到期 → 回收
    for inst in frozen:
        try:
            async with sm() as session:
                fresh = await orchestrator_service.get_instance(session, inst.user_id, inst.uuid)
                if fresh.status != "frozen":
                    continue
                balance = await wallet.get_balance(session, inst.user_id)
                if balance > 0:
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
            logger.exception("patrol_frozen_failed", instance_id=inst.id)


async def _patrol_disks(sm: async_sessionmaker[AsyncSession], counts: dict[str, int]) -> None:
    """数据盘欠费链路:欠费 → grace(7 天只读)→ frozen(30 天)→ 清除;回款即恢复。

    巡检集合取「名下有欠费链路上的盘」的用户(见 disks.list_arrears_chain_user_ids)。
    """
    from app.modules.orchestrator import service as orchestrator_service

    async with sm() as session:
        user_ids = await orchestrator_service.arrears_chain_disk_user_ids(session)
    for user_id in user_ids:
        try:
            async with sm() as session:
                balance = await wallet.get_balance(session, user_id)
                changed = await orchestrator_service.disks_arrears_transition(
                    session, user_id, balance <= 0
                )
                await session.commit()
                counts["disks"] += changed
        except Exception:
            logger.exception("patrol_disks_failed", user_id=user_id)
