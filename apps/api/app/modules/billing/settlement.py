"""结算引擎:事件驱动,幂等。计费依据是 instance_events。

三条入口:settle_due_hours(每小时 :02,advisory lock,从水位线追平到上一整点)、
计费边监听器 edge_listener(离开 running 即时尾账)、upsert_hour_bill(幂等入账原语)。
幂等:UNIQUE(instance_id, hour_start) + 秒数单调递增补差价。事件读取一律经 orchestrator.service。
追平:水位线越过但账未结清的窗口登记 settlement_gaps(catchup_truncated / dead_letter),
未核销数经 SETTLEMENT_GAP_UNRESOLVED 持续告警,缺口不自愈。
"""

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError, ErrorCode, conflict
from app.core.locks import LockKey, advisory_lock
from app.core.logging import get_logger
from app.core.metrics import (
    SETTLEMENT_FAILED_TOTAL,
    SETTLEMENT_GAP_UNRESOLVED,
    SETTLEMENT_LAG,
)
from app.core.money import as_amount, as_price, billing_units, disk_daily_charge
from app.core.pagination import Page, paginate_by_id
from app.core.timeutil import (
    BILLING_DAY_OFFSET,
    billing_day_floor,
    ensure_utc,
    hour_floor,
    now_utc,
    prev_hour_range,
)
from app.modules.billing import wallet
from app.modules.billing.models import BillHourly, SettlementGap, SettlementWatermark
from app.modules.billing.schemas import AdminSettlementGapOut

logger = get_logger(__name__)

RUNNING = "running"

# 追平上限:超出即记缺口表并只结最近这些窗口
MAX_CATCHUP_HOURS = 72
MAX_CATCHUP_DAYS = 14
# 同一 (窗口, 对象) 连续失败这么多轮即死信:记缺口后水位线允许越过
DEAD_LETTER_AFTER = 3
# worker/DB 时钟允许的最大偏差,超阈值本轮不结算
CLOCK_SKEW_MAX_SECONDS = 30.0

# (kind, window_start, object_id) → 连续失败轮数。进程内存;多副本由 advisory lock 串行
_failure_streaks: dict[tuple[str, datetime, int], int] = {}


def _billing_view(
    events: list[tuple[datetime, str | None, str, Any]],
) -> list[tuple[datetime, str | None, str]]:
    """事件流水 → 计费视图(3 元组)。node_lost/pod_lost 的退出边带 metadata.unready_since,
    计费截断到该时刻;尾账/整点/追平三条路径同口径。
    """
    out: list[tuple[datetime, str | None, str]] = []
    for created_at, from_status, to_status, meta in events:
        ts = ensure_utc(created_at)
        if from_status == RUNNING and meta and meta.get("unready_since"):
            unready_at = ensure_utc(datetime.fromisoformat(str(meta["unready_since"])))
            if unready_at < ts:
                ts = unready_at
        out.append((ts, from_status, to_status))
    return out


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

    total_us = 0  # 整数微秒累加(禁 float 中间态)
    for start, end in periods:
        s = max(start, window_start)
        e = min(end, window_end)
        if e > s:
            delta = e - s
            total_us += (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    # 微秒 → 秒:整数 HALF_EVEN
    q, r = divmod(total_us, 1_000_000)
    if r > 500_000 or (r == 500_000 and q % 2 == 1):
        q += 1
    return q


def bill_amount(
    unit_price: Decimal, gpu_count: int, seconds: int, *, max_seconds: int = 3600
) -> Decimal:
    """入账 2 位 HALF_EVEN。seconds ∈ [0, max_seconds](默认单整点小时窗口),越界报错不截断;
    max_seconds 只供巡检的多小时估算口径放宽。
    份数走 money.billing_units:GPU 实例 = 卡数,CPU 实例(gpu_count=0)= 1 份整机。
    """
    if not 0 <= seconds <= max_seconds:
        raise ValueError(f"seconds out of range: {seconds}")
    raw = (
        as_price(unit_price) * Decimal(billing_units(gpu_count)) * Decimal(seconds) / Decimal(3600)
    )
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
    detail_extra: dict[str, Any] | None = None,
) -> Decimal:
    """幂等入账原语。返回本次实际扣款金额(0 = 无新增)。

    无账单行 → 插入(RETURNING 判定新行)+ 全额扣款;已有行且 seconds 增长 → 行锁内更新 + 扣差价;
    seconds 未增长 → no-op。detail_extra 合并进 detail。调用方负责 commit。
    """
    if seconds <= 0:
        return Decimal("0.00")
    hour_start = hour_floor(hour_start)
    amount = bill_amount(unit_price, gpu_count, seconds)
    extra = detail_extra or {}

    inserted = (
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
                detail={"source": source, **extra},
            )
            .on_conflict_do_nothing(index_elements=["instance_id", "hour_start"])
            .returning(BillHourly.id)
        )
    ).scalar_one_or_none()
    if inserted is not None:
        row_id, charged = inserted, amount
    else:
        row = (
            await session.execute(
                select(BillHourly)
                .where(BillHourly.instance_id == instance_id, BillHourly.hour_start == hour_start)
                .with_for_update()
            )
        ).scalar_one()
        if seconds <= row.seconds_used:
            return Decimal("0.00")
        delta = as_amount(amount - row.amount)
        if delta <= 0:
            return Decimal("0.00")
        row.seconds_used = seconds
        row.amount = amount
        row.detail = {**(row.detail or {}), "source": source, **extra, "topped_up": True}
        row_id, charged = row.id, delta

    if charged <= 0:
        return Decimal("0.00")  # 秒数过少舍入为 0:留账单行,不产生扣款
    await wallet.debit(
        session,
        user_id,
        charged,
        type_="consume",
        ref_type="bill_hourly",
        ref_id=str(row_id),
        remark=f"实例 GPU 时费({source})",
        allow_negative=True,
        allow_frozen=True,  # 对已发生消费的收款
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
    detail_extra: dict[str, Any] | None = None,
) -> Decimal:
    """按事件重建窗口秒数并入账。窗口必须落在单一自然小时内。
    读事件前先拿实例行锁(orchestrator.service.lock_instance_for_billing)。
    """
    from app.modules.orchestrator import service as orchestrator_service

    await orchestrator_service.lock_instance_for_billing(session, instance_id)
    rows = await orchestrator_service.billing_events_before(session, instance_id, window_end)
    seconds = running_seconds_in_window(_billing_view(rows), window_start, window_end)
    return await upsert_hour_bill(
        session,
        instance_id=instance_id,
        user_id=user_id,
        unit_price=unit_price,
        gpu_count=gpu_count,
        hour_start=window_start,
        seconds=seconds,
        source=source,
        detail_extra=detail_extra,
    )


# 转包周期前允许结清的最大滞后小时数,超过即拒绝转换
MAX_CONVERT_SETTLE_HOURS = 48


async def settle_on_demand_up_to(
    session: AsyncSession,
    *,
    instance_id: int,
    user_id: int,
    unit_price: Decimal,
    gpu_count: int,
    at: datetime,
) -> Decimal:
    """把该实例截至 `at` 的按量账逐小时结清(水位线之后的第一个小时起)。返回本次扣款合计。
    转包周期前必须调它。滞后超过 MAX_CONVERT_SETTLE_HOURS 抛 CONFLICT。
    """
    watermark = await get_watermark(session, "hourly")
    last_hour = hour_floor(at)
    start = (
        last_hour
        if watermark is None
        else min(last_hour, ensure_utc(watermark) + timedelta(hours=1))
    )
    lag_hours = int((last_hour - start).total_seconds() // 3600)
    if lag_hours > MAX_CONVERT_SETTLE_HOURS:
        logger.error(
            "convert_blocked_settlement_behind", instance_id=instance_id, lag_hours=lag_hours
        )
        raise conflict(key="billing.settlementBehind")
    total = Decimal("0.00")
    cursor = start
    while cursor <= last_hour:
        total += await settle_instance_window(
            session,
            instance_id=instance_id,
            user_id=user_id,
            unit_price=unit_price,
            gpu_count=gpu_count,
            window_start=cursor,
            window_end=min(cursor + timedelta(hours=1), at),
            source="convert",
        )
        cursor += timedelta(hours=1)
    return total


async def reprice_current_hour(
    session: AsyncSession,
    *,
    instance_id: int,
    user_id: int,
    new_price: Decimal,
    gpu_count: int,
    at: datetime,
) -> Decimal:
    """把当前自然小时已出的账单行改按新单价重算,补扣差价。返回补扣金额。
    口径「一小时一价,以结算时的实例单价为准」;只在涨价时动这一行,降价时整行不动。
    """
    row = (
        await session.execute(
            select(BillHourly)
            .where(BillHourly.instance_id == instance_id, BillHourly.hour_start == hour_floor(at))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:  # 本小时还没出过账
        return Decimal("0.00")
    amount = bill_amount(new_price, gpu_count, row.seconds_used)
    delta = as_amount(amount - row.amount)
    if delta <= 0:
        return Decimal("0.00")  # 降价:整行不动
    row.unit_price = new_price
    row.amount = amount
    row.detail = {**(row.detail or {}), "repriced": True}
    await wallet.debit(
        session,
        user_id,
        delta,
        type_="consume",
        ref_type="bill_hourly",
        ref_id=str(row.id),
        remark="实例 GPU 时费(转按量补差价)",
        allow_negative=True,
        allow_frozen=True,  # 对已发生消费的收款
    )
    return delta


async def get_watermark(session: AsyncSession, key: str) -> datetime | None:
    row = await session.get(SettlementWatermark, key)
    return ensure_utc(row.settled_through) if row else None


async def _clock_skew_exceeded(sm: async_sessionmaker[AsyncSession]) -> bool:
    """worker/DB 时钟比对:偏差超 CLOCK_SKEW_MAX_SECONDS 时拒绝本轮结算并告警(返回 True)。"""
    async with sm() as session:
        db_now = ensure_utc((await session.execute(select(func.now()))).scalar_one())
    skew = abs((db_now - now_utc()).total_seconds())
    if skew <= CLOCK_SKEW_MAX_SECONDS:
        return False
    SETTLEMENT_FAILED_TOTAL.labels(kind="clock_skew").inc()
    logger.error(
        "settlement_clock_skew",
        skew_seconds=skew,
        hint="worker 与 DB 时钟偏差超阈值,本轮结算已拒绝;请校准 NTP 后重试",
    )
    return True


async def _advance_watermark(
    sm: async_sessionmaker[AsyncSession], key: str, value: datetime
) -> None:
    """水位线只前进不后退。"""
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


async def _record_gaps(
    sm: async_sessionmaker[AsyncSession],
    *,
    kind: str,
    windows: list[datetime],
    object_id: int,
    reason: str,
) -> None:
    """缺口登记(幂等,独立事务):同一 (kind, window, object) 只留一行。"""
    if not windows:
        return
    async with sm() as session:
        for w in windows:
            await session.execute(
                pg_insert(SettlementGap)
                .values(kind=kind, window_start=w, object_id=object_id, reason=reason)
                .on_conflict_do_nothing(index_elements=["kind", "window_start", "object_id"])
            )
        await session.commit()


async def _refresh_gap_gauge(session: AsyncSession) -> None:
    """未核销缺口 Gauge 全量刷新(DB 口径):结算任务每轮末与重放/核销后调用。"""
    rows = (
        (
            await session.execute(
                select(SettlementGap.kind, func.count())
                .where(SettlementGap.resolved_at.is_(None))
                .group_by(SettlementGap.kind)
            )
        )
        .tuples()
        .all()
    )
    counts = dict(rows)
    for kind in ("hourly", "daily_disk"):
        SETTLEMENT_GAP_UNRESOLVED.labels(kind=kind).set(counts.get(kind, 0))


SettleAttempt = Callable[[AsyncSession], Awaitable[Decimal]]


async def _settle_window_objects(
    sm: async_sessionmaker[AsyncSession],
    *,
    kind: str,
    window_start: datetime,
    attempts: list[tuple[int, SettleAttempt]],
) -> tuple[int, list[int]]:
    """逐对象独立事务结算一个窗口。返回 (入账数, 仍失败且未死信的对象 id 列表)。
    单对象失败不拖垮整窗;连续 DEAD_LETTER_AFTER 轮失败记缺口(dead_letter)后放过。
    """
    settled = 0
    failed: list[int] = []
    for object_id, attempt in attempts:
        try:
            async with sm() as session:
                charged = await attempt(session)
                await session.commit()
            if charged > 0:
                settled += 1
            _failure_streaks.pop((kind, window_start, object_id), None)
        except Exception:
            SETTLEMENT_FAILED_TOTAL.labels(kind=kind).inc()
            logger.exception(
                f"{kind}_settlement_failed",
                object_id=object_id,
                window_start=window_start.isoformat(),
            )
            key = (kind, window_start, object_id)
            streak = _failure_streaks.get(key, 0) + 1
            if streak >= DEAD_LETTER_AFTER:
                logger.error(
                    f"{kind}_settlement_dead_letter",
                    object_id=object_id,
                    window_start=window_start.isoformat(),
                    streak=streak,
                )
                await _record_gaps(
                    sm,
                    kind=kind,
                    windows=[window_start],
                    object_id=object_id,
                    reason="dead_letter",
                )
                _failure_streaks.pop(key, None)
            else:
                _failure_streaks[key] = streak
                failed.append(object_id)
    if settled:
        logger.info(
            f"{kind}_settlement_done", window_start=window_start.isoformat(), settled=settled
        )
    return settled, failed


async def _catchup_settle(
    sm: async_sessionmaker[AsyncSession],
    *,
    kind: str,
    lock_key: LockKey,
    target_start: datetime,
    step: timedelta,
    max_catchup: int,
    floor_fn: Callable[[datetime], datetime],
    settle_window: Callable[[datetime, datetime], Awaitable[tuple[int, list[int]]]],
) -> int:
    """追平主循环(小时/日结共用):水位线 → 截断记缺口 → 逐窗结算 → 连续推进水位线 → lag。
    水位线只能连续推进:某窗有未解决失败即停在它之前;截断与死信的跳窗都登记 settlement_gaps。
    """
    settled = 0
    async with advisory_lock(sm, lock_key) as got:
        if not got:
            return 0
        async with sm() as session:
            watermark = await get_watermark(session, kind)
        if watermark is None:
            # 无水位线:首次部署(窗口前没有任何可计费对象)只引导不登记缺口;
            # 有历史却无水位线 = 水位线行丢失,只结最近窗口并登记 watermark_missing 缺口
            from app.modules.orchestrator import service as orchestrator_service

            async with sm() as session:
                has_history = await orchestrator_service.billing_history_exists_before(
                    session, kind, target_start
                )
            if has_history:
                logger.warning(
                    f"{kind}_watermark_missing",
                    hint="无结算水位线但存在历史对象:水位线已丢失,只结最近窗口,更早窗口需人工核查补结",
                )
                await _record_gaps(
                    sm, kind=kind, windows=[target_start], object_id=0, reason="watermark_missing"
                )
            else:
                logger.info(f"{kind}_watermark_bootstrap", window_start=target_start.isoformat())
        first_start = target_start if watermark is None else floor_fn(watermark) + step
        floor_start = target_start - (max_catchup - 1) * step
        if first_start < floor_start:
            skipped: list[datetime] = []
            w = first_start
            while w < floor_start:
                skipped.append(w)
                w += step
            logger.error(
                f"{kind}_catchup_truncated",
                watermark=watermark.isoformat() if watermark else None,
                skipped_windows=len(skipped),
                hint="超出追平上限的窗口已登记 settlement_gaps,需补结任务或人工处理",
            )
            await _record_gaps(
                sm, kind=kind, windows=skipped, object_id=0, reason="catchup_truncated"
            )
            first_start = floor_start
        window_start = first_start
        contiguous_ok = True  # 水位线只能连续推进
        while window_start <= target_start:
            window_end = window_start + step
            charged, failed = await settle_window(window_start, window_end)
            settled += charged
            if not failed and contiguous_ok:
                await _advance_watermark(sm, kind, window_start)
            else:
                contiguous_ok = False
            window_start = window_end
        async with sm() as session:
            done_through = await get_watermark(session, kind)
            await _refresh_gap_gauge(session)
        lag = (
            0.0
            if done_through is None
            else (target_start - done_through).total_seconds() / step.total_seconds()
        )
        SETTLEMENT_LAG.labels(kind=kind).set(max(0.0, lag))
    return settled


def _hourly_attempt(
    inst_id: int,
    user_id: int,
    price: Decimal,
    gpu_count: int,
    window_start: datetime,
    window_end: datetime,
) -> SettleAttempt:
    """闭包绑定一个 (实例, 小时) 的结算参数;独立事务由 _settle_window_objects 开。"""

    async def attempt(session: AsyncSession) -> Decimal:
        return await settle_instance_window(
            session,
            instance_id=inst_id,
            user_id=user_id,
            unit_price=price,
            gpu_count=gpu_count,
            window_start=window_start,
            window_end=window_end,
            source="hourly",
        )

    return attempt


async def _hourly_window_attempts(
    sm: async_sessionmaker[AsyncSession], window_start: datetime, window_end: datetime
) -> list[tuple[int, SettleAttempt]]:
    """构造一个小时窗口内全部计费候选实例的入账闭包(整点结算与整窗重放共用)。"""
    from app.modules.orchestrator import service as orchestrator_service

    async with sm() as session:
        instances = await orchestrator_service.billing_candidates(session, window_start, window_end)
    return [
        (inst_id, _hourly_attempt(inst_id, user_id, price, gpu_count, window_start, window_end))
        for inst_id, user_id, price, gpu_count in instances
    ]


async def settle_due_hours(
    sm: async_sessionmaker[AsyncSession], *, at: datetime | None = None
) -> int:
    """从水位线追平结算到上一自然小时(相对 at,默认现在)。返回入账实例数合计。
    首次运行(无水位线)只结上一小时;某小时内有实例失败时水位线停在它之前,后续小时照常结算。
    """
    if await _clock_skew_exceeded(sm):
        return 0
    target_start, _ = prev_hour_range(at or now_utc())

    async def settle_window(window_start: datetime, window_end: datetime) -> tuple[int, list[int]]:
        attempts = await _hourly_window_attempts(sm, window_start, window_end)
        return await _settle_window_objects(
            sm, kind="hourly", window_start=window_start, attempts=attempts
        )

    return await _catchup_settle(
        sm,
        kind="hourly",
        lock_key=LockKey.HOURLY_SETTLEMENT,
        target_start=target_start,
        step=timedelta(hours=1),
        max_catchup=MAX_CATCHUP_HOURS,
        floor_fn=hour_floor,
        settle_window=settle_window,
    )


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

    day = billing_day_floor(day)
    # 累积差分公式按月内第几天取值,须传该计费日的北京日历日
    beijing_date = (day + BILLING_DAY_OFFSET).date()
    amount = disk_daily_charge(price_gb_month, size_gb, beijing_date)
    inserted = (
        await session.execute(
            pg_insert(BillDailyDisk)
            .values(
                disk_id=disk_id,
                user_id=user_id,
                day=day,
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
            allow_negative=True,
            allow_frozen=True,  # 对已发生消费的收款
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
    删盘与扩容前必须调用。下界取日结水位线,水位线缺失时以建盘日为下界。
    """
    target_day = billing_day_floor(at or now_utc())
    watermark = await get_watermark(session, "daily_disk")
    if watermark is None:
        first_day = billing_day_floor(ensure_utc(created_at))
    else:
        first_day = max(
            billing_day_floor(watermark) + timedelta(days=1),
            billing_day_floor(ensure_utc(created_at)),
        )
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


def _disk_attempt(
    disk_id: int, user_id: int, price: Decimal, size_gb: int, day: datetime
) -> SettleAttempt:
    """闭包绑定一个 (盘, 日) 的结算参数;独立事务由 _settle_window_objects 开。"""

    async def attempt(session: AsyncSession) -> Decimal:
        return await charge_disk_day(
            session,
            disk_id=disk_id,
            user_id=user_id,
            price_gb_month=price,
            size_gb=size_gb,
            day=day,
        )

    return attempt


async def _billable_disk_rows(
    session: AsyncSession,
) -> list[tuple[int, int, Decimal, int, datetime, datetime | None, datetime | None]]:
    """当前可计费盘的入账参数行(日结与整窗重放共用)。"""
    from app.modules.orchestrator import service as orchestrator_service

    disks = await orchestrator_service.billable_disks(session)
    return [
        (
            d.id,
            d.user_id,
            d.price_gb_month,
            d.size_gb,
            ensure_utc(d.created_at),
            ensure_utc(d.grace_started_at) if d.grace_started_at else None,
            ensure_utc(d.grace_ended_at) if d.grace_ended_at else None,
        )
        for d in disks
    ]


async def _daily_disk_window_attempts(
    sm: async_sessionmaker[AsyncSession],
    disk_rows: list[tuple[int, int, Decimal, int, datetime, datetime | None, datetime | None]],
    window_start: datetime,
    window_end: datetime,
) -> list[tuple[int, SettleAttempt]]:
    """构造一天窗口内全部盘的入账闭包(created/grace 过滤口径与日结/整窗重放共用)。"""
    attempts: list[tuple[int, SettleAttempt]] = []
    for disk_id, user_id, price, size_gb, created_at, grace_started, grace_ended in disk_rows:
        if created_at >= window_end:
            continue  # 该日之后创建的盘不出账
        if grace_started is not None:
            # 追平跨过 grace 的日子跳过区间内部日(登记缺口);边界日照常出账,
            # UNIQUE(disk_id, day) 幂等兜底
            g_start = billing_day_floor(grace_started)
            g_end = billing_day_floor(grace_ended) if grace_ended is not None else None
            if g_start < window_start and (g_end is None or window_start < g_end):
                await _record_gaps(
                    sm,
                    kind="daily_disk",
                    windows=[window_start],
                    object_id=disk_id,
                    reason="grace_overlap",
                )
                continue
        attempts.append((disk_id, _disk_attempt(disk_id, user_id, price, size_gb, window_start)))
    return attempts


async def settle_daily_disks(
    sm: async_sessionmaker[AsyncSession], *, at: datetime | None = None
) -> int:
    """数据盘日结:从水位线追平到上一自然日。UNIQUE(disk_id, day) 幂等,关机也扣。
    返回本轮实际扣款的「盘×日」数;截断/死信的跳窗登记 settlement_gaps。
    """
    if await _clock_skew_exceeded(sm):
        return 0
    target_day = billing_day_floor(at or now_utc()) - timedelta(days=1)  # 结算昨日(北京日界)
    # (disk_id, user_id, price, size_gb, created_at, grace_started_at, grace_ended_at)
    disk_rows: (
        list[tuple[int, int, Decimal, int, datetime, datetime | None, datetime | None]] | None
    ) = None

    async def settle_window(window_start: datetime, window_end: datetime) -> tuple[int, list[int]]:
        nonlocal disk_rows
        if disk_rows is None:
            # 首个窗口才拉盘清单,轮内不变
            async with sm() as session:
                disk_rows = await _billable_disk_rows(session)
        attempts = await _daily_disk_window_attempts(sm, disk_rows, window_start, window_end)
        return await _settle_window_objects(
            sm, kind="daily_disk", window_start=window_start, attempts=attempts
        )

    return await _catchup_settle(
        sm,
        kind="daily_disk",
        lock_key=LockKey.DAILY_DISK_SETTLEMENT,
        target_start=target_day,
        step=timedelta(days=1),
        max_catchup=MAX_CATCHUP_DAYS,
        floor_fn=billing_day_floor,
        settle_window=settle_window,
    )


# ---------- 结算缺口闭环(管理端:查询 / 重放 / 人工核销) ----------


async def admin_list_gaps(
    session: AsyncSession,
    *,
    kind: str | None = None,
    reason: str | None = None,
    unresolved_only: bool = True,
    cursor: str | None = None,
    limit: int | None = None,
) -> Page[AdminSettlementGapOut]:
    """缺口列表(游标分页,降序)。默认只看未核销。"""
    stmt = select(SettlementGap).order_by(SettlementGap.id.desc())
    if kind:
        stmt = stmt.where(SettlementGap.kind == kind)
    if reason:
        stmt = stmt.where(SettlementGap.reason == reason)
    if unresolved_only:
        stmt = stmt.where(SettlementGap.resolved_at.is_(None))
    page_items, next_cursor = await paginate_by_id(
        session, stmt, id_col=SettlementGap.id, cursor=cursor, limit=limit
    )
    return Page[AdminSettlementGapOut](
        items=[AdminSettlementGapOut.model_validate(r) for r in page_items],
        next_cursor=next_cursor,
    )


async def replay_gap(
    sm: async_sessionmaker[AsyncSession],
    gap_id: int,
    *,
    operator_id: int,
) -> AdminSettlementGapOut:
    """重放缺口窗口的幂等入账原语(管理端人工触发),成功回写 resolved_at。

    - dead_letter(object_id>0):按 (kind, window, object) 精确补结;
    - catchup_truncated / watermark_missing(object_id=0):对该窗全量候选重放
      (daily_disk 按当前可计费盘口径,已删除盘的当日账不在其列);
    - grace_overlap:拒绝重放(409,走人工核销)。
    返回 schema 而非 ORM 行(本函数自建 session)。
    """
    from app.modules.orchestrator import service as orchestrator_service

    async with sm() as session:
        gap = await session.get(SettlementGap, gap_id, with_for_update=True)
        if gap is None:
            raise AppError(
                ErrorCode.NOT_FOUND, key="billing.settlementGapNotFound", http_status=404
            )
        if gap.resolved_at is not None:
            return AdminSettlementGapOut.model_validate(gap)  # 已核销直接返回
        if gap.reason == "grace_overlap":
            raise conflict(key="billing.settlementGapNotReplayable", params={"reason": gap.reason})
        kind, window_start, object_id = gap.kind, ensure_utc(gap.window_start), gap.object_id

    if kind == "hourly":
        window_end = window_start + timedelta(hours=1)
        if object_id:
            async with sm() as session:
                row = await orchestrator_service.instance_billing_snapshot(session, object_id)
            if row is None:
                raise conflict(
                    key="billing.settlementGapObjectGone", params={"objectId": str(object_id)}
                )
            inst_id, user_id, price, gpu_count = row
            async with sm() as session:
                await settle_instance_window(
                    session,
                    instance_id=inst_id,
                    user_id=user_id,
                    unit_price=price,
                    gpu_count=gpu_count,
                    window_start=window_start,
                    window_end=window_end,
                    source="gap_replay",
                    detail_extra={"gap_id": gap_id, "replayed_by": operator_id},
                )
                await session.commit()
        else:
            attempts = await _hourly_window_attempts(sm, window_start, window_end)
            await _settle_window_objects(
                sm, kind="hourly", window_start=window_start, attempts=attempts
            )
    elif kind == "daily_disk":
        day = billing_day_floor(window_start)
        if object_id:
            async with sm() as session:
                disk_row = await orchestrator_service.disk_billing_snapshot(session, object_id)
                if disk_row is None:
                    raise conflict(
                        key="billing.settlementGapObjectGone", params={"objectId": str(object_id)}
                    )
                disk_id, disk_user_id, disk_price, disk_size = disk_row
                await charge_disk_day(
                    session,
                    disk_id=disk_id,
                    user_id=disk_user_id,
                    price_gb_month=disk_price,
                    size_gb=disk_size,
                    day=day,
                )
                await session.commit()
        else:
            async with sm() as session:
                disk_rows = await _billable_disk_rows(session)
            attempts = await _daily_disk_window_attempts(
                sm, disk_rows, day, day + timedelta(days=1)
            )
            await _settle_window_objects(sm, kind="daily_disk", window_start=day, attempts=attempts)
    else:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="common.validation")

    # 回写 resolved_at(行锁内)
    async with sm() as session:
        gap = await session.get(SettlementGap, gap_id, with_for_update=True)
        if gap is None:
            raise AppError(
                ErrorCode.NOT_FOUND, key="billing.settlementGapNotFound", http_status=404
            )
        if gap.resolved_at is None:
            gap.resolved_at = now_utc()
            await session.commit()
            logger.info("settlement_gap_replayed", gap_id=gap_id, operator_id=operator_id)
        out = AdminSettlementGapOut.model_validate(gap)
        await _refresh_gap_gauge(session)
    return out


async def resolve_gap(
    session: AsyncSession,
    gap_id: int,
    *,
    note: str,
    operator_id: int,
) -> SettlementGap:
    """人工核销(不重放)。说明必填,写审计。"""
    gap = await session.get(SettlementGap, gap_id, with_for_update=True)
    if gap is None:
        raise AppError(ErrorCode.NOT_FOUND, key="billing.settlementGapNotFound", http_status=404)
    if gap.resolved_at is None:
        gap.resolved_at = now_utc()
        await session.commit()
        logger.info("settlement_gap_resolved", gap_id=gap_id, operator_id=operator_id, note=note)
    await _refresh_gap_gauge(session)
    return gap
