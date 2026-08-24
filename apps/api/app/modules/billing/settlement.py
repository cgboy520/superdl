"""结算引擎:事件驱动,幂等。计费依据是 instance_events,不依赖 Prometheus。

三条入口:
1. settle_due_hours(sm) —— 每小时 :02 定时任务(advisory lock),从水位线追平到上一整点
2. 计费边监听器(edge_listener) —— 离开 running 即时尾账(与状态迁移同事务)
3. upsert_hour_bill(...) —— 共用的幂等入账原语

幂等设计:UNIQUE(instance_id, hour_start) + 秒数单调递增补差价。
同一小时先尾账后整点结算、重复执行、并发执行,都只补不重扣。
事件读取一律经 orchestrator.service 只读接口(模块边界)。

追平设计:结算窗口由 settlement_watermarks 水位线推进而非只结上一个窗口,worker 停机
跨过整点/日结时刻时漏掉的窗口下一轮自动补上。水位线被越过但账未结清的窗口一律登记
settlement_gaps(追平截断 catchup_truncated / 单对象连续失败死信 dead_letter),
并计 SETTLEMENT_GAP_TOTAL 单调计数 —— 缺口不自愈、可告警,由补结任务或人工处理。
"""

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.locks import LockKey, try_advisory_lock
from app.core.logging import get_logger
from app.core.metrics import SETTLEMENT_FAILED_TOTAL, SETTLEMENT_GAP_TOTAL, SETTLEMENT_LAG
from app.core.money import as_amount, as_price, disk_daily_charge
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

logger = get_logger(__name__)

RUNNING = "running"

# 追平上限:超出即记缺口表并只结最近这些窗口,防停机数月后一轮拖垮 worker
MAX_CATCHUP_HOURS = 72
MAX_CATCHUP_DAYS = 14
# 同一 (窗口, 对象) 连续失败这么多轮即死信:记缺口后水位线允许越过,
# 防一个坏对象永久卡住水位线(进而在追平上限外触发整段截断)
DEAD_LETTER_AFTER = 3

# (kind, window_start, object_id) → 连续失败轮数。进程内存:worker 重启只是多验几轮
# (方向安全);多副本由 advisory lock 串行,各副本各记各的,最坏死信推迟几轮
_failure_streaks: dict[tuple[str, datetime, int], int] = {}


def _billing_view(
    events: list[tuple[datetime, str | None, str, Any]],
) -> list[tuple[datetime, str | None, str]]:
    """事件流水 → 计费视图(3 元组)。

    node_lost/pod_lost 的退出边带 metadata.unready_since(Pod 首次 not-ready 时刻):
    判定前的宽限观察期实例已不可用,属平台责任时段,计费截断到该时刻而非判定时刻。
    截断写在事件重建层,尾账/整点/追平三条结算路径口径天然一致(整点重算不会把
    尾账已截断的秒数再补回来)。
    """
    out: list[tuple[datetime, str | None, str]] = []
    running_entry: datetime | None = None
    for created_at, from_status, to_status, meta in events:
        ts = ensure_utc(created_at)
        if to_status == RUNNING:
            running_entry = ts
        if from_status == RUNNING and meta and meta.get("unready_since"):
            unready_at = ensure_utc(datetime.fromisoformat(str(meta["unready_since"])))
            # 早于本次进入 running 的 unready_since 是上次失联的残留,不参与截断
            stale = running_entry is not None and unready_at < running_entry
            if not stale and unready_at < ts:
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

    total_us = 0  # 整数微秒累加(计费链路禁 float 中间态);timedelta 三元组精确无舍入
    for start, end in periods:
        s = max(start, window_start)
        e = min(end, window_end)
        if e > s:
            delta = e - s
            total_us += (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    # 微秒 → 秒:整数 HALF_EVEN(与金额 as_amount 同一舍入族,不截断也不经 float)
    q, r = divmod(total_us, 1_000_000)
    if r > 500_000 or (r == 500_000 and q % 2 == 1):
        q += 1
    return q


def bill_amount(unit_price: Decimal, gpu_count: int, seconds: int) -> Decimal:
    """入账 2 位 HALF_EVEN。seconds ∈ [0, 3600](单整点小时窗口);越界即窗口计算有 bug,报错不截断。"""
    if not 0 <= seconds <= 3600:
        raise ValueError(f"seconds out of range: {seconds}")
    raw = as_price(unit_price) * Decimal(gpu_count) * Decimal(seconds) / Decimal(3600)
    return as_amount(raw)


def bill_amount_window(unit_price: Decimal, gpu_count: int, seconds: int) -> Decimal:
    """多小时窗口的估算口径(巡检停机判据用,永不入账):与 bill_amount 同公式,
    seconds 上限放宽到 31 天,越界同样报错不截断。入账一律走 bill_amount 逐窗。"""
    if not 0 <= seconds <= 31 * 24 * 3600:
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
    detail_extra: dict[str, Any] | None = None,
) -> Decimal:
    """幂等入账原语。返回本次实际扣款金额(0 = 无新增)。

    - 无账单行 → 插入 + 全额扣款
    - 已有行且 seconds 增长 → 更新行 + 扣差价(同小时先尾账后续跑的场景)
    - seconds 未增长 → no-op(重放安全)
    detail_extra:入账依据的附加留痕(如失联截断的 unready_since),合并进 detail。
    调用方负责 commit。
    """
    if seconds <= 0:
        return Decimal("0.00")
    hour_start = hour_floor(hour_start)
    amount = bill_amount(unit_price, gpu_count, seconds)
    extra = detail_extra or {}

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
        row.detail = {**(row.detail or {}), "source": source, **extra, "topped_up": True}
        charged = delta
    else:
        row.seconds_used = max(row.seconds_used, seconds)
        row.amount = bill_amount(unit_price, gpu_count, row.seconds_used)
        row.detail = {**(row.detail or {}), "source": source, **extra, "charged": True}
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
        allow_negative=True,  # 结算扣款允许透支
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

    读事件前先拿实例行锁(见 orchestrator.service.lock_instance_for_billing)。
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


async def _record_gaps(
    sm: async_sessionmaker[AsyncSession],
    *,
    kind: str,
    windows: list[datetime],
    object_id: int,
    reason: str,
) -> None:
    """缺口登记(幂等,独立事务):同一 (kind, window, object) 只留一行;指标单调不降。"""
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
    SETTLEMENT_GAP_TOTAL.labels(kind=kind, reason=reason).inc(len(windows))


SettleAttempt = Callable[[AsyncSession], Awaitable[Decimal]]


async def _settle_window_objects(
    sm: async_sessionmaker[AsyncSession],
    *,
    kind: str,
    window_start: datetime,
    attempts: list[tuple[int, SettleAttempt]],
) -> tuple[int, list[int]]:
    """逐对象独立事务结算一个窗口。返回 (入账数, 仍失败且未死信的对象 id 列表)。

    单对象失败不拖垮整窗;连续 DEAD_LETTER_AFTER 轮失败记缺口(dead_letter)后放过,
    水位线得以越过 —— 缺口表是后续补结的依据。
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

    水位线只能连续推进:某窗有未解决失败即停在它之前,下轮重试(入账幂等,不重扣);
    截断与死信的跳窗都登记 settlement_gaps,SETTLEMENT_GAP_TOTAL 单调不降(告警不自愈)。
    """
    settled = 0
    async with (
        sm() as lock_session,
        try_advisory_lock(lock_session, lock_key) as got,
    ):
        if not got:
            return 0
        async with sm() as session:
            watermark = await get_watermark(session, kind)
        if watermark is None:
            # 无水位线两种来源不可区分:首次部署引导(正常)/ 水位线行被误删或库回退(异常)。
            # 两种情形本轮都只结最近窗口,更早窗口不自动补,显式留痕供告警匹配。
            logger.warning(
                f"{kind}_watermark_missing",
                hint="无结算水位线:首次部署属正常引导;若非首次部署则水位线已丢失,"
                "只结最近窗口,更早窗口需人工核查补结",
            )
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
        contiguous_ok = True  # 水位线只能连续推进:中间某窗失败即停在它之前
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


async def settle_due_hours(
    sm: async_sessionmaker[AsyncSession], *, at: datetime | None = None
) -> int:
    """从水位线追平结算到上一自然小时(相对 at,默认现在)。返回入账实例数合计。

    首次运行(无水位线)只结上一小时;此后每轮把 worker 停机期间漏掉的小时逐个补上。
    某小时内有实例结算失败时水位线停在它之前(下一轮重试),后续小时照常结算——
    入账是幂等的,重复结算不会重扣;连续失败超限的 (实例, 小时) 死信进 settlement_gaps。
    """
    from app.modules.orchestrator import service as orchestrator_service

    target_start, _ = prev_hour_range(at or now_utc())

    async def settle_window(window_start: datetime, window_end: datetime) -> tuple[int, list[int]]:
        async with sm() as session:
            instances = await orchestrator_service.billing_candidates(
                session, window_start, window_end
            )
        attempts = [
            (inst_id, _hourly_attempt(inst_id, user_id, price, gpu_count, window_start, window_end))
            for inst_id, user_id, price, gpu_count in instances
        ]
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
    # (billing_day_floor 折回 UTC 后 .day 会差一天)
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
            allow_negative=True,  # 结算扣款允许透支
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

    删盘与扩容前必须调用:日结只对结算时点仍存活的盘、按结算时点容量出账。
    下界取日结水位线而非建盘日,欠费冻结期这类有意不计费的日子不补回来。
    """
    target_day = billing_day_floor(at or now_utc())
    watermark = await get_watermark(session, "daily_disk")
    if watermark is None:
        first_day = target_day
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


async def settle_daily_disks(
    sm: async_sessionmaker[AsyncSession], *, at: datetime | None = None
) -> int:
    """数据盘日结:从水位线追平到上一自然日。UNIQUE(disk_id, day) 幂等,关机也扣。

    返回本轮实际扣款的「盘×日」数。停机跨过 00:10 的日子由水位线在下一轮补上;
    截断/死信的跳窗登记 settlement_gaps。
    """
    from app.modules.orchestrator import service as orchestrator_service

    target_day = billing_day_floor(at or now_utc()) - timedelta(days=1)  # 结算昨日(北京日界)
    # (disk_id, user_id, price, size_gb, created_at, grace_started_at, grace_ended_at)
    disk_rows: (
        list[tuple[int, int, Decimal, int, datetime, datetime | None, datetime | None]] | None
    ) = None

    async def settle_window(window_start: datetime, window_end: datetime) -> tuple[int, list[int]]:
        nonlocal disk_rows
        if disk_rows is None:
            # 首个窗口才拉盘清单(此时已持 advisory lock),轮内不变
            async with sm() as session:
                disks = await orchestrator_service.billable_disks(session)
                disk_rows = [
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
        attempts = []
        for disk_id, user_id, price, size_gb, created_at, grace_started, grace_ended in disk_rows:
            if created_at >= window_end:
                continue  # 该日之后创建的盘不出账
            if grace_started is not None:
                # 追平跨过 grace 的日子按「grace 不计费」跳过区间内部日(登记缺口人工核查)。
                # 边界日(进入/恢复当日)照常出账:进入时已结清、恢复日应计;UNIQUE(disk_id, day)
                # 幂等兜底,不会重复扣款
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
            attempts.append(
                (disk_id, _disk_attempt(disk_id, user_id, price, size_gb, window_start))
            )
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
