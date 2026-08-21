"""结算引擎:事件驱动,幂等。计费依据是 instance_events,不依赖 Prometheus。

三条入口:
1. settle_due_hours(sm) —— 每小时 :02 定时任务(advisory lock),从水位线追平到上一整点
2. 计费边监听器(edge_listener) —— 离开 running 即时尾账(与状态迁移同事务)
3. upsert_hour_bill(...) —— 共用的幂等入账原语

幂等设计:UNIQUE(instance_id, hour_start) + 秒数单调递增补差价。
同一小时先尾账后整点结算、重复执行、并发执行,都只补不重扣。
事件读取一律经 orchestrator.service 只读接口(模块边界)。

追平设计:结算窗口由 settlement_watermarks 水位线推进,不是「只结上一个窗口」——
worker 重启/停机恰好跨过整点(或跨过日结时刻)时,漏掉的窗口下一轮自动补上。
超过追平上限的窗口只能人工补,此时 SETTLEMENT_LAG 指标已持续告警。
"""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.locks import LockKey, try_advisory_lock
from app.core.logging import get_logger
from app.core.metrics import SETTLEMENT_FAILED_TOTAL, SETTLEMENT_LAG
from app.core.money import as_amount, as_price, disk_daily_charge
from app.core.timeutil import day_floor, ensure_utc, hour_floor, now_utc, prev_hour_range
from app.modules.billing import wallet
from app.modules.billing.models import BillHourly, SettlementWatermark

logger = get_logger(__name__)

RUNNING = "running"

# 追平上限:超出即只结最近这些窗口(并打 error + 指标),防停机数月后一轮拖垮 worker
MAX_CATCHUP_HOURS = 72
MAX_CATCHUP_DAYS = 14


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
    """入账 2 位 HALF_EVEN。seconds ∈ [0, 3600](单整点小时窗口);越界即窗口计算有 bug,报错不截断。"""
    if not 0 <= seconds <= 3600:
        raise ValueError(f"seconds out of range: {seconds}")
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
    """按事件重建窗口秒数并入账。窗口必须落在单一自然小时内。

    读事件之前必须先拿实例行锁 —— 事件流是计费主依据,而在飞的状态迁移事务对无锁读
    是不可见的;读到一个「少了最后那条 stopping」的事件流会算出偏高的秒数,而入账是
    单调只增的,高估值再也回不去。详见 lock_instance_for_billing 的注释。
    """
    from app.modules.orchestrator import service as orchestrator_service

    await orchestrator_service.lock_instance_for_billing(session, instance_id)
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


async def get_watermark(session: AsyncSession, key: str) -> datetime | None:
    row = await session.get(SettlementWatermark, key)
    return ensure_utc(row.settled_through) if row else None


async def _advance_watermark(
    sm: async_sessionmaker[AsyncSession], key: str, value: datetime
) -> None:
    """水位线只前进不后退(多副本/补跑并存时的兜底)。"""
    async with sm() as session:
        await session.execute(
            pg_insert(SettlementWatermark)
            .values(key=key, settled_through=value)
            .on_conflict_do_update(
                index_elements=["key"],
                set_={"settled_through": value},
                where=SettlementWatermark.settled_through < value,
            )
        )
        await session.commit()


async def _settle_one_hour(
    sm: async_sessionmaker[AsyncSession], window_start: datetime, window_end: datetime
) -> tuple[int, bool]:
    """结算单个自然小时窗口。返回 (入账实例数, 是否全部成功)。"""
    from app.modules.orchestrator import service as orchestrator_service

    settled = 0
    all_ok = True
    async with sm() as session:
        instances = await orchestrator_service.billing_candidates(session, window_start, window_end)
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
            all_ok = False
            logger.exception("hourly_settlement_failed", instance_id=inst_id)
            SETTLEMENT_FAILED_TOTAL.labels(kind="hourly").inc()
    if settled:
        logger.info("hourly_settlement_done", hour=window_start.isoformat(), settled=settled)
    return settled, all_ok


async def settle_due_hours(
    sm: async_sessionmaker[AsyncSession], *, at: datetime | None = None
) -> int:
    """从水位线追平结算到上一自然小时(相对 at,默认现在)。返回入账实例数合计。

    首次运行(无水位线)只结上一小时;此后每轮把 worker 停机期间漏掉的小时逐个补上。
    某小时内有实例结算失败时水位线停在它之前(下一轮重试),后续小时照常结算——
    入账是幂等的,重复结算不会重扣。
    """
    target_start, _ = prev_hour_range(at or now_utc())
    settled = 0
    async with (
        sm() as lock_session,
        try_advisory_lock(lock_session, LockKey.HOURLY_SETTLEMENT) as got,
    ):
        if not got:
            return 0
        async with sm() as session:
            watermark = await get_watermark(session, "hourly")
        first_start = (
            target_start if watermark is None else hour_floor(watermark) + timedelta(hours=1)
        )
        floor_start = target_start - timedelta(hours=MAX_CATCHUP_HOURS - 1)
        if first_start < floor_start:
            logger.error(
                "hourly_settlement_catchup_truncated",
                watermark=watermark.isoformat() if watermark else None,
                skipped_hours=int((floor_start - first_start).total_seconds() // 3600),
                hint="超出追平上限的小时只能人工补结,漏账已发生",
            )
            first_start = floor_start
        window_start = first_start
        contiguous_ok = True  # 水位线只能连续推进:中间某小时失败即停在它之前
        while window_start <= target_start:
            window_end = window_start + timedelta(hours=1)
            charged, ok = await _settle_one_hour(sm, window_start, window_end)
            settled += charged
            if ok and contiguous_ok:
                await _advance_watermark(sm, "hourly", window_start)
            else:
                contiguous_ok = False
            window_start = window_end
        async with sm() as session:
            done_through = await get_watermark(session, "hourly")
        lag = 0.0 if done_through is None else (target_start - done_through).total_seconds() / 3600
        SETTLEMENT_LAG.labels(kind="hourly").set(max(0.0, lag))
    return settled


async def charge_disk_day(
    session: AsyncSession,
    *,
    disk_id: int,
    user_id: int,
    price_gb_month: Decimal,
    size_gb: int,
    day: datetime,
) -> Decimal:
    """单盘单日入账原语。UNIQUE(disk_id, day) 幂等,返回本次扣款(0 = 该日已出过账)。

    插入与扣款同一事务(RETURNING 判定新行);调用方负责 commit。
    """
    from app.modules.billing.models import BillDailyDisk

    amount = disk_daily_charge(price_gb_month, size_gb)
    inserted = (
        await session.execute(
            pg_insert(BillDailyDisk)
            .values(
                disk_id=disk_id,
                user_id=user_id,
                day=day_floor(day),
                size_gb=size_gb,
                unit_price=as_price(price_gb_month),
                amount=amount,
            )
            .on_conflict_do_nothing(index_elements=["disk_id", "day"])
            .returning(BillDailyDisk.id)
        )
    ).scalar_one_or_none()
    if inserted is None:
        return Decimal("0.00")
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
    return amount


async def settle_disk_pending_days(
    session: AsyncSession,
    *,
    disk_id: int,
    user_id: int,
    price_gb_month: Decimal,
    size_gb: int,
    created_at: datetime,
    at: datetime | None = None,
) -> Decimal:
    """结清该盘截至今日、尚未出账的自然日(同事务调用,不 commit)。返回扣款合计。

    删盘与扩容前必须调用,否则两个口径漏洞成立:
    - 日结只对「结算时点仍存活」的盘出账 → 当日建、当日删可循环零费用占用存储;
    - 日结按结算时点容量出账 → 扩容会把更大的容量追溯到尚未出账的旧日期(多扣用户)。
    下界取日结水位线而非建盘日:欠费冻结期这类「有意不计费」的日子不会被补回来。
    """
    target_day = day_floor(at or now_utc())
    watermark = await get_watermark(session, "daily_disk")
    if watermark is None:
        first_day = target_day
    else:
        first_day = max(day_floor(watermark) + timedelta(days=1), day_floor(ensure_utc(created_at)))
    first_day = max(first_day, target_day - timedelta(days=MAX_CATCHUP_DAYS - 1))
    total = Decimal("0.00")
    day = first_day
    while day <= target_day:
        total += await charge_disk_day(
            session,
            disk_id=disk_id,
            user_id=user_id,
            price_gb_month=price_gb_month,
            size_gb=size_gb,
            day=day,
        )
        day += timedelta(days=1)
    return total


async def settle_daily_disks(
    sm: async_sessionmaker[AsyncSession], *, at: datetime | None = None
) -> int:
    """数据盘日结:从水位线追平到上一自然日。UNIQUE(disk_id, day) 幂等,关机也扣。

    返回本轮实际扣款的「盘×日」数。停机跨过 00:10 的日子由水位线在下一轮补上。
    """
    from app.modules.orchestrator import service as orchestrator_service

    target_day = day_floor(at or now_utc()) - timedelta(days=1)  # 结算昨日
    settled = 0
    async with (
        sm() as lock_session,
        try_advisory_lock(lock_session, LockKey.DAILY_DISK_SETTLEMENT) as got,
    ):
        if not got:
            return 0
        async with sm() as session:
            watermark = await get_watermark(session, "daily_disk")
            disks = await orchestrator_service.billable_disks(session)
            disk_rows = [
                (d.id, d.user_id, d.price_gb_month, d.size_gb, ensure_utc(d.created_at))
                for d in disks
            ]
        first_day = target_day if watermark is None else day_floor(watermark) + timedelta(days=1)
        floor_day = target_day - timedelta(days=MAX_CATCHUP_DAYS - 1)
        if first_day < floor_day:
            logger.error(
                "daily_disk_catchup_truncated",
                watermark=watermark.isoformat() if watermark else None,
                skipped_days=(floor_day - first_day).days,
                hint="超出追平上限的日期只能人工补结,漏账已发生",
            )
            first_day = floor_day
        day = first_day
        contiguous_ok = True
        while day <= target_day:
            day_ok = True
            day_settled = 0
            for disk_id, user_id, price, size_gb, created_at in disk_rows:
                if created_at >= day + timedelta(days=1):
                    continue  # 该日之后创建的盘不出账
                try:
                    async with sm() as session:
                        charged = await charge_disk_day(
                            session,
                            disk_id=disk_id,
                            user_id=user_id,
                            price_gb_month=price,
                            size_gb=size_gb,
                            day=day,
                        )
                        await session.commit()
                    if charged > 0:
                        day_settled += 1
                except Exception:
                    day_ok = False
                    logger.exception("daily_disk_settlement_failed", disk_id=disk_id, day=day)
                    SETTLEMENT_FAILED_TOTAL.labels(kind="daily_disk").inc()
            if day_ok and contiguous_ok:
                await _advance_watermark(sm, "daily_disk", day)
            else:
                contiguous_ok = False
            if day_settled:
                logger.info("daily_disk_settlement_done", day=day.isoformat(), settled=day_settled)
            settled += day_settled
            day += timedelta(days=1)
        async with sm() as session:
            done_through = await get_watermark(session, "daily_disk")
        lag = 0.0 if done_through is None else (target_day - done_through).total_seconds() / 86400
        SETTLEMENT_LAG.labels(kind="daily_disk").set(max(0.0, lag))
    return settled
