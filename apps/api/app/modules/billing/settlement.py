"""Hourly settlement, daily disk settlement and gap handling based on instance_events; events are
read through orchestrator.queries.

Bills and debits share a transaction, idempotent per object and window; unsettled windows the
catch-up passes are recorded in settlement_gaps.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
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
from app.core.servercopy import copy as server_copy
from app.core.sqlutil import get_for_update_or_404
from app.core.timeutil import (
    billing_day_floor,
    billing_day_shift,
    billing_local_date,
    ensure_utc,
    hour_floor,
    now_utc,
    prev_hour_range,
)
from app.modules.billing import wallet
from app.modules.billing.models import BillDailyDisk, BillHourly, SettlementGap, SettlementWatermark
from app.modules.billing.schemas import AdminSettlementGapOut
from app.modules.orchestrator import queries as orchestrator_queries

logger = get_logger(__name__)

RUNNING = "running"
CREATING = "creating"
STARTING = "starting"
FAILED = "failed"
OCCUPIED_SINCE_KEY = "occupied_since"

MAX_CATCHUP_HOURS = 72
MAX_CATCHUP_DAYS = 14
DEAD_LETTER_AFTER = 3
CLOCK_SKEW_MAX_SECONDS = 30.0

_failure_streaks: dict[tuple[str, datetime, int], int] = {}


def truncated_at(
    edge_at: datetime, from_status: str | None, meta: Mapping[str, Any] | None
) -> datetime:
    """UTC billing end; when leaving running with unready_since earlier than the edge, the former
    wins."""
    edge_at = ensure_utc(edge_at)
    if from_status == RUNNING and meta and meta.get("unready_since"):
        unready_at = ensure_utc(datetime.fromisoformat(str(meta["unready_since"])))
        if unready_at < edge_at:
            return unready_at
    return edge_at


def occupied_since(
    from_status: str | None, to_status: str, meta: Mapping[str, Any] | None
) -> datetime | None:
    """A creating/starting → failed edge carrying `occupied_since` returns that UTC instant
    (start of a service instance whose health_path never passed while the container ran); other
    edges return None."""
    if to_status != FAILED or from_status not in (CREATING, STARTING) or not meta:
        return None
    raw = meta.get(OCCUPIED_SINCE_KEY)
    if not raw:
        return None
    return ensure_utc(datetime.fromisoformat(str(raw)))


def billing_view(
    events: list[tuple[datetime, str | None, str, Any]],
) -> list[tuple[datetime, str | None, str]]:
    """Event rows → billing view (3-tuples): exit edges truncated at truncated_at;
    a failure edge with occupied_since expands into "enter running at occupied_since + leave
    running at the edge time"."""
    view: list[tuple[datetime, str | None, str]] = []
    for created_at, from_status, to_status, meta in events:
        edge_at = ensure_utc(created_at)
        since = occupied_since(from_status, to_status, meta)
        if since is not None and since < edge_at:
            view.append((since, from_status, RUNNING))
            view.append((edge_at, RUNNING, to_status))
            continue
        view.append((truncated_at(created_at, from_status, meta), from_status, to_status))
    return view


def running_seconds_in_window(
    events: list[tuple[datetime, str | None, str]],
    window_start: datetime,
    window_end: datetime,
) -> int:
    """Rebuild the running seconds within [window_start, window_end) from the event rows.

    events: every event of the instance up to window_end (created_at, from_status, to_status), in
    occurrence order.
    Still running counts up to window_end; whole microseconds are summed, then rounded HALF_EVEN to
    seconds.
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

    total_us = 0
    for start, end in periods:
        s = max(start, window_start)
        e = min(end, window_end)
        if e > s:
            delta = e - s
            total_us += (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    q, r = divmod(total_us, 1_000_000)
    if r > 500_000 or (r == 500_000 and q % 2 == 1):
        q += 1
    return q


def bill_amount(
    unit_price: Decimal, gpu_count: int, seconds: int, *, max_seconds: int = 3600
) -> Decimal:
    """Amount from hourly price and seconds, 2 dp HALF_EVEN; out-of-range seconds raise ValueError.

    gpu_count=0 bills one whole-machine unit, otherwise per card.
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
    """Post idempotently per instance and hour, returning the amount actually charged; the caller
    commits.

    A new row charges the full amount; an existing row tops up the difference under the row lock
    only when seconds and amount both grew. Zero-amount bills write no debit.
    detail_extra is merged into detail.
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
        return Decimal("0.00")
    await _charge_bill(
        session,
        user_id,
        charged,
        ref_type="bill_hourly",
        ref_id=row_id,
        remark=server_copy("billing.remark.hourly", source=source),
    )
    return charged


async def _charge_bill(
    session: AsyncSession, user_id: int, amount: Decimal, *, ref_type: str, ref_id: int, remark: str
) -> None:
    """Settlement debit for consumption that already happened; overdraft and balance below frozen
    allowed, no commit."""
    await wallet.debit(
        session,
        user_id,
        amount,
        type_="consume",
        ref_type=ref_type,
        ref_id=str(ref_id),
        remark=remark,
        allow_negative=True,
        allow_frozen=True,
    )


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
    """Lock the instance row first, then rebuild the event seconds and post; the window must lie
    within one calendar hour, the caller commits."""
    await orchestrator_queries.lock_instance_for_billing(session, instance_id)
    rows = await orchestrator_queries.billing_events(session, instance_id)
    seconds = running_seconds_in_window(billing_view(rows), window_start, window_end)
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
    """Settle the instance's on-demand bill up to `at` hour by hour (from the first hour after the
    watermark). Returns the total charged.
    Must be called before converting to a subscription. Lagging beyond MAX_CONVERT_SETTLE_HOURS
    raises CONFLICT.
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
    """Recompute the already billed row of the current calendar hour at the new unit price and
    charge the difference. Returns the top-up.
    Rule "one price per hour, the instance unit price at settlement time"; the row changes only
    when the price rose, a price cut leaves it alone.
    """
    row = (
        await session.execute(
            select(BillHourly)
            .where(BillHourly.instance_id == instance_id, BillHourly.hour_start == hour_floor(at))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        return Decimal("0.00")
    amount = bill_amount(new_price, gpu_count, row.seconds_used)
    delta = as_amount(amount - row.amount)
    if delta <= 0:
        return Decimal("0.00")
    row.unit_price = new_price
    row.amount = amount
    row.detail = {**(row.detail or {}), "repriced": True}
    await _charge_bill(
        session,
        user_id,
        delta,
        ref_type="bill_hourly",
        ref_id=row.id,
        remark=server_copy("billing.remark.reprice"),
    )
    return delta


async def get_watermark(session: AsyncSession, key: str) -> datetime | None:
    row = await session.get(SettlementWatermark, key)
    return ensure_utc(row.settled_through) if row else None


async def _clock_skew_exceeded(sm: async_sessionmaker[AsyncSession]) -> bool:
    """worker / DB clock comparison: a skew above CLOCK_SKEW_MAX_SECONDS refuses this round's
    settlement and alerts (returns True)."""
    async with sm() as session:
        db_now = ensure_utc((await session.execute(select(func.now()))).scalar_one())
    skew = abs((db_now - now_utc()).total_seconds())
    if skew <= CLOCK_SKEW_MAX_SECONDS:
        return False
    SETTLEMENT_FAILED_TOTAL.labels(kind="clock_skew").inc()
    logger.error(
        "settlement_clock_skew",
        skew_seconds=skew,
        hint="worker and DB clocks differ beyond the threshold, this settlement round was refused;"
        " fix NTP and retry",
    )
    return True


async def _advance_watermark(
    sm: async_sessionmaker[AsyncSession], key: str, value: datetime
) -> None:
    """The watermark only moves forward."""
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
    """Record a gap (idempotent, independent transaction): one row per (kind, window, object)."""
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
    """Full refresh of the unresolved-gap gauge (DB view): called at the end of every settlement
    round
    and after replay / write-off."""
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
    """Settle one window object by object in independent transactions. Returns (posted count, ids
    still failing and not yet dead-lettered).
    One failure does not take the window down; DEAD_LETTER_AFTER consecutive failures record a
    dead_letter gap and let it pass.
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
    shift: Callable[[datetime, int], datetime] | None = None,
) -> int:
    """Catch up window by window under the advisory lock; the watermark advances only past windows
    that succeeded or recorded a gap.

    Without a watermark only the target window is settled, with earlier history a
    watermark_missing gap is recorded; windows beyond the cap record a truncation gap.
    `shift(window_start, n)` steps windows (default `n × step`; daily windows pass a calendar
    shift so DST days keep one window per local date); `step` only sizes the lag gauge.
    """
    move = shift or (lambda start, n: start + n * step)
    settled = 0
    async with advisory_lock(sm, lock_key) as got:
        if not got:
            return 0
        async with sm() as session:
            watermark = await get_watermark(session, kind)
        if watermark is None:
            async with sm() as session:
                has_history = await orchestrator_queries.billing_history_exists_before(
                    session, kind, target_start
                )
            if has_history:
                logger.warning(
                    f"{kind}_watermark_missing",
                    hint="no settlement watermark but historical objects exist: the watermark was"
                    " lost,"
                    " only the most recent window is settled, earlier windows need manual review",
                )
                await _record_gaps(
                    sm, kind=kind, windows=[target_start], object_id=0, reason="watermark_missing"
                )
            else:
                logger.info(f"{kind}_watermark_bootstrap", window_start=target_start.isoformat())
        first_start = target_start if watermark is None else move(floor_fn(watermark), 1)
        floor_start = move(target_start, -(max_catchup - 1))
        if first_start < floor_start:
            skipped: list[datetime] = []
            w = first_start
            while w < floor_start:
                skipped.append(w)
                w = move(w, 1)
            logger.error(
                f"{kind}_catchup_truncated",
                watermark=watermark.isoformat() if watermark else None,
                skipped_windows=len(skipped),
                hint="windows beyond the catch-up cap were recorded in settlement_gaps, they need a"
                " replay task or manual handling",
            )
            await _record_gaps(
                sm, kind=kind, windows=skipped, object_id=0, reason="catchup_truncated"
            )
            first_start = floor_start
        window_start = first_start
        contiguous_ok = True
        while window_start <= target_start:
            window_end = move(window_start, 1)
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
    """Closure binding the settlement parameters of one (instance, hour); _settle_window_objects
    opens the independent transaction."""

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
    """Build the posting closures of every billing candidate in one hour window (shared by
    clock-hour settlement and whole-window replay)."""
    async with sm() as session:
        instances = await orchestrator_queries.billing_candidates(session, window_start)
    return [
        (inst_id, _hourly_attempt(inst_id, user_id, price, gpu_count, window_start, window_end))
        for inst_id, user_id, price, gpu_count in instances
    ]


async def settle_due_hours(
    sm: async_sessionmaker[AsyncSession], *, at: datetime | None = None
) -> int:
    """Catch up from the watermark to the previous calendar hour (relative to at, default now).
    Returns the total of posted instances.
    The first run (no watermark) settles the previous hour only; when an instance fails within an
    hour the watermark stops before it, later hours settle as usual.
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
    """Single disk, single day posting primitive. UNIQUE(disk_id, day) idempotent, returns the
    amount charged (0 = that day was already billed).
    Insert and debit share the transaction (RETURNING decides a new row); the caller commits.
    """
    day = billing_day_floor(day)
    amount = disk_daily_charge(price_gb_month, size_gb, billing_local_date(day))
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
        await _charge_bill(
            session,
            user_id,
            amount,
            ref_type="bill_daily_disk",
            ref_id=inserted,
            remark=server_copy("billing.remark.disk_daily"),
        )
    return amount


@dataclass(frozen=True)
class DiskBillingInput:
    """Identity, price, capacity and creation-time snapshot needed for daily disk settlement."""

    id: int
    user_id: int
    price_gb_month: Decimal
    size_gb: int
    created_at: datetime


async def settle_disk_pending_days(
    session: AsyncSession, disk: DiskBillingInput, *, at: datetime | None = None
) -> Decimal:
    """Settle disk fees up to the billing day of at, catching up at most MAX_CATCHUP_DAYS days;
    returns the total charged, no commit.

    Starts at the later of the day after the watermark and the creation day; without a watermark
    at the creation day.
    Must be called in the same transaction before deletion, expansion or entering the arrears
    grace.
    """
    target_day = billing_day_floor(at or now_utc())
    watermark = await get_watermark(session, "daily_disk")
    if watermark is None:
        first_day = billing_day_floor(ensure_utc(disk.created_at))
    else:
        first_day = max(
            billing_day_shift(billing_day_floor(watermark), 1),
            billing_day_floor(ensure_utc(disk.created_at)),
        )
    first_day = max(first_day, billing_day_shift(target_day, -(MAX_CATCHUP_DAYS - 1)))
    total = Decimal("0.00")
    day = first_day
    while day <= target_day:
        total += await charge_disk_day(
            session,
            disk_id=disk.id,
            user_id=disk.user_id,
            price_gb_month=disk.price_gb_month,
            size_gb=disk.size_gb,
            day=day,
        )
        day = billing_day_shift(day, 1)
    return total


def _disk_attempt(
    disk_id: int, user_id: int, price: Decimal, size_gb: int, day: datetime
) -> SettleAttempt:
    """Closure binding the settlement parameters of one (disk, day); _settle_window_objects opens
    the independent transaction."""

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
    """Posting parameter rows of the currently billable disks (shared by daily settlement and
    whole-window replay)."""
    disks = await orchestrator_queries.billable_disks(session)
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
    """Build the daily closures of already created disks; days inside a grace interval record
    grace_overlap, boundary days settle as usual."""
    attempts: list[tuple[int, SettleAttempt]] = []
    for disk_id, user_id, price, size_gb, created_at, grace_started, grace_ended in disk_rows:
        if created_at >= window_end:
            continue
        if grace_started is not None:
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
    """Daily disk settlement: catch up from the watermark to the previous calendar day.
    UNIQUE(disk_id, day) idempotent, charged even when stopped.
    Returns the number of (disk × day) actually charged this round; truncation / dead-letter skips
    record settlement_gaps.
    """
    if await _clock_skew_exceeded(sm):
        return 0
    target_day = billing_day_shift(billing_day_floor(at or now_utc()), -1)
    disk_rows: (
        list[tuple[int, int, Decimal, int, datetime, datetime | None, datetime | None]] | None
    ) = None

    async def settle_window(window_start: datetime, window_end: datetime) -> tuple[int, list[int]]:
        nonlocal disk_rows
        if disk_rows is None:
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
        shift=billing_day_shift,
    )


async def admin_list_gaps(
    session: AsyncSession,
    *,
    kind: str | None = None,
    reason: str | None = None,
    unresolved_only: bool = True,
    cursor: str | None = None,
    limit: int | None = None,
) -> Page[AdminSettlementGapOut]:
    """Gap list (cursor pagination, descending). Unresolved only by default."""
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
    """Replay a single-object or whole-window gap in an independent transaction, then record
    resolved_at under the gap row lock.

    grace_overlap → 409; whole disk windows use the currently billable disks. A single-object
    failure inside a whole window is recorded by the settler
    and does not stop this function from resolving the original gap. Returns the admin view.
    """
    async with sm() as session:
        gap = await get_for_update_or_404(
            session, SettlementGap, gap_id, key="billing.settlementGapNotFound"
        )
        if gap.resolved_at is not None:
            return AdminSettlementGapOut.model_validate(gap)
        if gap.reason == "grace_overlap":
            raise conflict(key="billing.settlementGapNotReplayable", params={"reason": gap.reason})
        kind, window_start, object_id = gap.kind, ensure_utc(gap.window_start), gap.object_id

    replay_detail = {"gap_id": gap_id, "replayed_by": operator_id}
    if kind == "hourly":
        await _replay_hourly_gap(sm, window_start, object_id, detail_extra=replay_detail)
    elif kind == "daily_disk":
        await _replay_daily_disk_gap(sm, billing_day_floor(window_start), object_id)
    else:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="common.validation")

    async with sm() as session:
        gap = await get_for_update_or_404(
            session, SettlementGap, gap_id, key="billing.settlementGapNotFound"
        )
        if gap.resolved_at is None:
            gap.resolved_at = now_utc()
            await session.commit()
            logger.info("settlement_gap_replayed", gap_id=gap_id, operator_id=operator_id)
        out = AdminSettlementGapOut.model_validate(gap)
        await _refresh_gap_gauge(session)
    return out


async def _replay_hourly_gap(
    sm: async_sessionmaker[AsyncSession],
    window_start: datetime,
    object_id: int,
    *,
    detail_extra: dict[str, Any],
) -> None:
    """Hourly gap: object_id>0 settles exactly one instance; =0 replays every candidate of the
    window."""
    window_end = window_start + timedelta(hours=1)
    if not object_id:
        attempts = await _hourly_window_attempts(sm, window_start, window_end)
        await _settle_window_objects(
            sm, kind="hourly", window_start=window_start, attempts=attempts
        )
        return
    async with sm() as session:
        row = await orchestrator_queries.instance_billing_snapshot(session, object_id)
    if row is None:
        raise conflict(key="billing.settlementGapObjectGone", params={"objectId": str(object_id)})
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
            detail_extra=detail_extra,
        )
        await session.commit()


async def _replay_daily_disk_gap(
    sm: async_sessionmaker[AsyncSession], day: datetime, object_id: int
) -> None:
    """Daily disk gap: object_id>0 settles exactly one disk; =0 replays the day for every currently
    billable disk."""
    if not object_id:
        async with sm() as session:
            disk_rows = await _billable_disk_rows(session)
        attempts = await _daily_disk_window_attempts(sm, disk_rows, day, billing_day_shift(day, 1))
        await _settle_window_objects(sm, kind="daily_disk", window_start=day, attempts=attempts)
        return
    async with sm() as session:
        disk_row = await orchestrator_queries.disk_billing_snapshot(session, object_id)
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


async def resolve_gap(
    session: AsyncSession,
    gap_id: int,
    *,
    note: str,
    operator_id: int,
) -> SettlementGap:
    """Record the gap as resolved under the row lock and commit, without replay; the caller
    validates the note and writes the audit."""
    gap = await get_for_update_or_404(
        session, SettlementGap, gap_id, key="billing.settlementGapNotFound"
    )
    if gap.resolved_at is None:
        gap.resolved_at = now_utc()
        await session.commit()
        logger.info("settlement_gap_resolved", gap_id=gap_id, operator_id=operator_id, note=note)
    await _refresh_gap_gauge(session)
    return gap
