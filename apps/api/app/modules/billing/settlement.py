"""结算引擎:事件驱动,幂等。计费依据是 instance_events,不依赖 Prometheus。

三条入口:
1. settle_previous_hour(sm) —— 每小时 :02 定时任务(advisory lock)
2. 计费边监听器(edge_listener) —— 离开 running 即时尾账(与状态迁移同事务)
3. upsert_hour_bill(...) —— 共用的幂等入账原语

幂等设计:UNIQUE(instance_id, hour_start) + 秒数单调递增补差价。
同一小时先尾账后整点结算、重复执行、并发执行,都只补不重扣。
事件读取一律经 orchestrator.service 只读接口(模块边界)。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.locks import LockKey, try_advisory_lock
from app.core.logging import get_logger
from app.core.metrics import SETTLEMENT_FAILED_TOTAL
from app.core.money import as_amount, as_price
from app.core.timeutil import ensure_utc, hour_floor, now_utc, prev_hour_range
from app.modules.billing import wallet
from app.modules.billing.models import BillHourly

logger = get_logger(__name__)

RUNNING = "running"


def running_seconds_in_window(
    events: list[tuple[datetime, str | None, str]],
    window_start: datetime,
    window_end: datetime,
) -> int:
    """从事件流水重建 [window_start, window_end) 内的 running 秒数。

    events:该实例截至 window_end 的全部事件 (created_at, from_status, to_status),按发生序。
    仍在 running(最后一次进入未离开)则计到 window_end。
    """
    window_start = ensure_utc(window_start)
    window_end = ensure_utc(window_end)
    periods: list[tuple[datetime, datetime]] = []
    running_since: datetime | None = None
    for created_at, from_status, to_status in events:
        ts = ensure_utc(created_at)
        if ts >= window_end:
            break
        if to_status == RUNNING and running_since is None:
            running_since = ts
        elif from_status == RUNNING and running_since is not None:
            periods.append((running_since, ts))
            running_since = None
    if running_since is not None:
        periods.append((running_since, window_end))

    total = 0.0
    for start, end in periods:
        s = max(start, window_start)
        e = min(end, window_end)
        if e > s:
            total += (e - s).total_seconds()
    return int(total)


def bill_amount(unit_price: Decimal, gpu_count: int, seconds: int) -> Decimal:
    """入账 2 位 HALF_EVEN。seconds clamp 到 [0, 3600] 防御。"""
    seconds = max(0, min(seconds, 3600))
    raw = as_price(unit_price) * Decimal(gpu_count) * Decimal(seconds) / Decimal(3600)
    return as_amount(raw)


async def upsert_hour_bill(
    session: AsyncSession,
    *,
    instance_id: int,
    user_id: int,
    unit_price: Decimal,
    gpu_count: int,
    hour_start: datetime,
    seconds: int,
    source: str,
) -> Decimal:
    """幂等入账原语。返回本次实际扣款金额(0 = 无新增)。

    - 无账单行 → 插入 + 全额扣款
    - 已有行且 seconds 增长 → 更新行 + 扣差价(同小时先尾账后续跑的场景)
    - seconds 未增长 → no-op(重放安全)
    调用方负责 commit。
    """
    if seconds <= 0:
        return Decimal("0.00")
    hour_start = hour_floor(hour_start)
    amount = bill_amount(unit_price, gpu_count, seconds)

    await session.execute(
        pg_insert(BillHourly)
        .values(
            instance_id=instance_id,
            user_id=user_id,
            hour_start=hour_start,
            seconds_used=seconds,
            unit_price=unit_price,
            gpu_count=gpu_count,
            amount=amount,
            detail={"source": source},
        )
        .on_conflict_do_nothing(index_elements=["instance_id", "hour_start"])
    )
    row = (
        await session.execute(
            select(BillHourly)
            .where(BillHourly.instance_id == instance_id, BillHourly.hour_start == hour_start)
            .with_for_update()
        )
    ).scalar_one()

    already_charged = bool(row.detail and row.detail.get("charged"))
    if already_charged:
        if seconds <= row.seconds_used:
            return Decimal("0.00")
        delta = as_amount(amount - row.amount)
        if delta <= 0:
            return Decimal("0.00")
        row.seconds_used = seconds
        row.amount = amount
        row.detail = {**(row.detail or {}), "source": source, "topped_up": True}
        charged = delta
    else:
        row.seconds_used = max(row.seconds_used, seconds)
        row.amount = bill_amount(unit_price, gpu_count, row.seconds_used)
        row.detail = {**(row.detail or {}), "source": source, "charged": True}
        charged = row.amount

    if charged <= 0:
        return Decimal("0.00")  # 秒数过少舍入为 0:留账单行(0.00),不产生扣款
    await wallet.debit(
        session,
        user_id,
        charged,
        type_="consume",
        ref_type="bill_hourly",
        ref_id=str(row.id),
        remark=f"实例 GPU 时费({source})",
    )
    return charged


async def settle_instance_window(
    session: AsyncSession,
    *,
    instance_id: int,
    user_id: int,
    unit_price: Decimal,
    gpu_count: int,
    window_start: datetime,
    window_end: datetime,
    source: str,
) -> Decimal:
    """按事件重建窗口秒数并入账。窗口必须落在单一自然小时内。"""
    from app.modules.orchestrator import service as orchestrator_service

    events = await orchestrator_service.billing_events_before(session, instance_id, window_end)
    seconds = running_seconds_in_window(events, window_start, window_end)
    return await upsert_hour_bill(
        session,
        instance_id=instance_id,
        user_id=user_id,
        unit_price=unit_price,
        gpu_count=gpu_count,
        hour_start=window_start,
        seconds=seconds,
        source=source,
    )


async def settle_previous_hour(
    sm: async_sessionmaker[AsyncSession], *, at: datetime | None = None
) -> int:
    """结算上一自然小时(相对 at,默认现在)。返回入账实例数。"""
    from app.modules.orchestrator import service as orchestrator_service

    window_start, window_end = prev_hour_range(at or now_utc())
    settled = 0
    async with (
        sm() as lock_session,
        try_advisory_lock(lock_session, LockKey.HOURLY_SETTLEMENT) as got,
    ):
        if not got:
            return 0
        async with sm() as session:
            instances = await orchestrator_service.billing_candidates(
                session, window_start, window_end
            )
        for inst_id, user_id, price, gpu_count in instances:
            # 每实例独立事务:单个失败不拖垮整轮
            try:
                async with sm() as session:
                    charged = await settle_instance_window(
                        session,
                        instance_id=inst_id,
                        user_id=user_id,
                        unit_price=price,
                        gpu_count=gpu_count,
                        window_start=window_start,
                        window_end=window_end,
                        source="hourly",
                    )
                    await session.commit()
                    if charged > 0:
                        settled += 1
            except Exception:
                logger.exception("hourly_settlement_failed", instance_id=inst_id)
                SETTLEMENT_FAILED_TOTAL.labels(kind="hourly").inc()
    if settled:
        logger.info("hourly_settlement_done", hour=window_start.isoformat(), settled=settled)
    return settled


async def settle_daily_disks(
    sm: async_sessionmaker[AsyncSession], *, at: datetime | None = None
) -> int:
    """数据盘日结:对上一自然日,UNIQUE(disk_id, day) 幂等入账。关机也扣(「日常费用」)。

    插入与扣款同一事务(RETURNING 判定新行),重复执行零重复扣款。
    """
    from datetime import timedelta

    from app.core.timeutil import day_floor
    from app.modules.billing.models import BillDailyDisk
    from app.modules.orchestrator import service as orchestrator_service

    now = at or now_utc()
    day = day_floor(now) - timedelta(days=1)  # 结算昨日
    settled = 0
    async with (
        sm() as lock_session,
        try_advisory_lock(lock_session, LockKey.DAILY_DISK_SETTLEMENT) as got,
    ):
        if not got:
            return 0
        async with sm() as session:
            disks = await orchestrator_service.billable_disks(session)
            disk_rows = [
                (d.id, d.user_id, d.price_gb_month, d.size_gb, d.created_at) for d in disks
            ]
        for disk_id, user_id, price, size_gb, created_at in disk_rows:
            if ensure_utc(created_at) >= day + timedelta(days=1):
                continue  # 当日结算窗口之后创建的盘不出账
            try:
                async with sm() as session:
                    amount = as_amount(as_price(price) * Decimal(size_gb) / Decimal(30))
                    inserted = (
                        await session.execute(
                            pg_insert(BillDailyDisk)
                            .values(
                                disk_id=disk_id,
                                user_id=user_id,
                                day=day,
                                size_gb=size_gb,
                                unit_price=price,
                                amount=amount,
                            )
                            .on_conflict_do_nothing(index_elements=["disk_id", "day"])
                            .returning(BillDailyDisk.id)
                        )
                    ).scalar_one_or_none()
                    if inserted is None:
                        continue  # 已结算(幂等)
                    if amount > 0:
                        await wallet.debit(
                            session,
                            user_id,
                            amount,
                            type_="consume",
                            ref_type="bill_daily_disk",
                            ref_id=str(inserted),
                            remark="数据盘日常费用",
                        )
                    await session.commit()
                    settled += 1
            except Exception:
                logger.exception("daily_disk_settlement_failed", disk_id=disk_id)
                SETTLEMENT_FAILED_TOTAL.labels(kind="daily_disk").inc()
    if settled:
        logger.info("daily_disk_settlement_done", day=day.isoformat(), settled=settled)
    return settled
