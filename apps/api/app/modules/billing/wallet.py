"""Wallet primitives: the single entry point of every balance change.

Updates must `SELECT ... FOR UPDATE` and write balance_ledger (balance_after snapshot) in the same
transaction. Nothing here commits.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import ColumnElement, Select, case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.logging import get_logger
from app.core.money import as_amount, disk_daily_charge, hourly_cost, money_label, money_str
from app.core.pagination import Page, RawPage, paginate_by_id
from app.core.platform_config import get_runtime_config
from app.core.pricing import MARKET_SUBSCRIPTION
from app.core.timeutil import now_utc
from app.modules.billing.models import (
    BalanceLedger,
    BillDailyDisk,
    BillHourly,
    Order,
    Subscription,
    Wallet,
)
from app.modules.billing.schemas import BillHourlyOut, BillSummaryItem, LedgerEntryOut
from app.modules.orchestrator import queries as orchestrator_queries

logger = get_logger(__name__)


async def _wallet_row(session: AsyncSession, user_id: int, *, lock: bool) -> Wallet:
    """The wallet row, created on first use (INSERT ... ON CONFLICT DO NOTHING, then re-read).
    lock=True takes FOR UPDATE with populate_existing (forces re-reading the locked values).
    """
    stmt = select(Wallet).where(Wallet.user_id == user_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    wallet = (await session.execute(stmt)).scalar_one_or_none()
    if wallet is None:
        await session.execute(
            pg_insert(Wallet)
            .values(user_id=user_id)
            .on_conflict_do_nothing(index_elements=["user_id"])
        )
        wallet = (await session.execute(stmt)).scalar_one()
    return wallet


async def get_or_create_wallet(session: AsyncSession, user_id: int) -> Wallet:
    return await _wallet_row(session, user_id, lock=False)


async def lock_wallet(session: AsyncSession, user_id: int) -> Wallet:
    """FOR UPDATE lock on the wallet row (created first when missing)."""
    return await _wallet_row(session, user_id, lock=True)


def _ledger(
    wallet: Wallet,
    type_: str,
    amount: Decimal,
    ref_type: str | None,
    ref_id: str | None,
    remark: str | None,
) -> BalanceLedger:
    return BalanceLedger(
        user_id=wallet.user_id,
        type=type_,
        amount=amount,
        balance_after=wallet.balance,
        ref_type=ref_type,
        ref_id=ref_id,
        remark=remark,
    )


async def credit(
    session: AsyncSession,
    user_id: int,
    amount: Decimal,
    *,
    type_: str,
    ref_type: str | None = None,
    ref_id: str | None = None,
    remark: str | None = None,
) -> Wallet:
    amount = as_amount(amount)
    if amount <= 0:
        raise ValueError("credit amount must be positive")
    wallet = await lock_wallet(session, user_id)
    wallet.balance = as_amount(wallet.balance + amount)
    session.add(_ledger(wallet, type_, amount, ref_type, ref_id, remark))
    return wallet


async def debit(
    session: AsyncSession,
    user_id: int,
    amount: Decimal,
    *,
    type_: str = "consume",
    ref_type: str | None = None,
    ref_id: str | None = None,
    remark: str | None = None,
    allow_negative: bool,
    allow_frozen: bool = False,
) -> BalanceLedger:
    """Debit under the wallet row lock and write the ledger row, returning the flushed row; no
    commit.

    allow_negative=False forbids a negative balance; allow_frozen=False forbids a balance below
    frozen.
    Only settlement of consumption that already happened (hourly and daily disk) may enable both;
    prepaid consumption must not.
    """
    amount = as_amount(amount)
    if amount <= 0:
        raise ValueError("debit amount must be positive")
    wallet = await lock_wallet(session, user_id)
    new_balance = as_amount(wallet.balance - amount)
    if not allow_negative and new_balance < 0:
        raise AppError(ErrorCode.INSUFFICIENT_BALANCE, key="billing.insufficientBalance")
    if not allow_frozen and new_balance < wallet.frozen:
        raise AppError(
            ErrorCode.INSUFFICIENT_BALANCE,
            key="billing.insufficientAvailableFrozen",
            params={"frozen": money_label(wallet.frozen)},
        )
    wallet.balance = new_balance
    entry = _ledger(wallet, type_, -amount, ref_type, ref_id, remark)
    session.add(entry)
    await session.flush()
    return entry


async def get_balance(session: AsyncSession, user_id: int) -> Decimal:
    wallet = (
        await session.execute(select(Wallet).where(Wallet.user_id == user_id))
    ).scalar_one_or_none()
    return wallet.balance if wallet else Decimal("0.00")


def available_of(wallet: Wallet) -> Decimal:
    """Available balance = balance - frozen."""
    return as_amount(wallet.balance - wallet.frozen)


async def get_available_balance(session: AsyncSession, user_id: int) -> Decimal:
    wallet = (
        await session.execute(select(Wallet).where(Wallet.user_id == user_id))
    ).scalar_one_or_none()
    return available_of(wallet) if wallet else Decimal("0.00")


async def refundable_capacity(session: AsyncSession, user_id: int) -> Decimal:
    """Net ledger excluding positive adjustments, floored at zero; refund requests and payouts are
    capped by it."""
    total = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (BalanceLedger.type == "adjust", func.least(BalanceLedger.amount, 0)),
                            else_=BalanceLedger.amount,
                        )
                    ),
                    0,
                )
            ).where(BalanceLedger.user_id == user_id)
        )
    ).scalar_one()
    return max(Decimal("0.00"), as_amount(Decimal(total)))


async def freeze(
    session: AsyncSession, user_id: int, amount: Decimal, *, ref_id: str, remark: str
) -> Wallet:
    """Freeze the same amount (channel reversal): balance untouched, no ledger row; frozen may
    exceed
    balance.
    Idempotency is guaranteed by the caller (order row flag)."""
    amount = as_amount(amount)
    if amount <= 0:
        raise ValueError("freeze amount must be positive")
    wallet = await lock_wallet(session, user_id)
    wallet.frozen = as_amount(wallet.frozen + amount)
    logger.error("wallet_frozen", user_id=user_id, amount=str(amount), ref_id=ref_id, remark=remark)
    return wallet


async def release_freeze(session: AsyncSession, user_id: int, amount: Decimal) -> Wallet:
    """Unfreeze (release write-off). Floor 0."""
    amount = as_amount(amount)
    if amount <= 0:
        raise ValueError("release amount must be positive")
    wallet = await lock_wallet(session, user_id)
    wallet.frozen = as_amount(max(Decimal("0.00"), wallet.frozen - amount))
    return wallet


async def assert_can_afford(
    session: AsyncSession,
    user_id: int,
    *,
    additional_hourly: Decimal = Decimal("0.00"),
    additional_daily_disk: Decimal = Decimal("0.00"),
) -> None:
    """Lock the wallet, then check the available balance covers the fees of existing, pending and
    new resources; no debit.

    Hourly fees × afford_cover_hours, daily disk fees × disk_grace_days; running subscription
    instances carry no hourly fee.
    Shortfall raises INSUFFICIENT_BALANCE with params balance/required/inflight.
    The caller must create or start the resource and commit in the same transaction.
    """
    locked = await lock_wallet(session, user_id)
    policies = await get_runtime_config(session)

    running = await orchestrator_queries.running_instances_of_user(session, user_id)
    pending = await orchestrator_queries.pending_hourly(session, user_id)
    inflight_hourly = (
        sum(
            (
                hourly_cost(i.price_hourly, i.gpu_count)
                for i in running
                if i.market != MARKET_SUBSCRIPTION
            ),
            Decimal("0.00"),
        )
        + pending
    )
    inflight_daily = sum(
        (
            disk_daily_charge(d.price_gb_month, d.size_gb)
            for d in await orchestrator_queries.billable_disks_of_user(session, user_id)
        ),
        Decimal("0.00"),
    )

    inflight = as_amount(inflight_hourly * policies.afford_cover_hours) + as_amount(
        inflight_daily * policies.disk_grace_days
    )
    required = (
        inflight
        + as_amount(as_amount(additional_hourly) * policies.afford_cover_hours)
        + as_amount(as_amount(additional_daily_disk) * policies.disk_grace_days)
    )
    if available_of(locked) < required:
        raise AppError(
            ErrorCode.INSUFFICIENT_BALANCE,
            key="billing.insufficientForInFlight",
            params={
                "balance": money_label(available_of(locked)),
                "required": money_label(required),
                "inflight": money_label(inflight),
            },
        )


async def ledger_page(
    session: AsyncSession, user_id: int, *, cursor: str | None = None, limit: int | None = None
):
    """Ledger cursor pagination (shared by the user and admin sides)."""
    stmt = (
        select(BalanceLedger)
        .where(BalanceLedger.user_id == user_id)
        .order_by(BalanceLedger.id.desc())
    )
    page_items, next_cursor = await paginate_by_id(
        session, stmt, id_col=BalanceLedger.id, cursor=cursor, limit=limit
    )
    return Page[LedgerEntryOut](
        items=[LedgerEntryOut.model_validate(r) for r in page_items], next_cursor=next_cursor
    )


async def hourly_bills_page(
    session: AsyncSession,
    user_id: int,
    *,
    instance_id: int | None = None,
    instance_ids: Sequence[int] | None = None,
    month_range: tuple | None = None,
    cursor: str | None = None,
    limit: int | None = None,
):
    """Hourly bill cursor pagination (shared by the user and admin sides). instance_ids = the union
    of a set of instances; an empty list means no bills."""
    stmt = select(BillHourly).where(BillHourly.user_id == user_id).order_by(BillHourly.id.desc())
    if instance_id is not None:
        stmt = stmt.where(BillHourly.instance_id == instance_id)
    if instance_ids is not None:
        if not instance_ids:
            return Page[BillHourlyOut](items=[], next_cursor=None)
        stmt = stmt.where(BillHourly.instance_id.in_(list(instance_ids)))
    if month_range is not None:
        stmt = stmt.where(
            BillHourly.hour_start >= month_range[0], BillHourly.hour_start < month_range[1]
        )
    page_items, next_cursor = await paginate_by_id(
        session, stmt, id_col=BillHourly.id, cursor=cursor, limit=limit
    )
    names = await orchestrator_queries.instance_names(session, [r.instance_id for r in page_items])
    items = []
    for r in page_items:
        out = BillHourlyOut.model_validate(r)
        out.instance_name = names.get(r.instance_id)
        items.append(out)
    return Page[BillHourlyOut](items=items, next_cursor=next_cursor)


@dataclass(frozen=True)
class ConsumptionSummary:
    gpu_total: Decimal
    disk_total: Decimal
    items: list[BillSummaryItem]


async def consumption_summary(
    session: AsyncSession, user_id: int, start: datetime, end: datetime
) -> ConsumptionSummary:
    """Sum instance hourly fees and daily disk fees of [start, end) by bill attribution period;
    items carry instance names and billed seconds."""
    gpu_rows = (
        (
            await session.execute(
                select(
                    BillHourly.instance_id,
                    func.sum(BillHourly.amount),
                    func.sum(BillHourly.seconds_used),
                )
                .where(
                    BillHourly.user_id == user_id,
                    BillHourly.hour_start >= start,
                    BillHourly.hour_start < end,
                )
                .group_by(BillHourly.instance_id)
            )
        )
        .tuples()
        .all()
    )
    disk_total = (
        await session.execute(
            select(func.coalesce(func.sum(BillDailyDisk.amount), 0)).where(
                BillDailyDisk.user_id == user_id,
                BillDailyDisk.day >= start,
                BillDailyDisk.day < end,
            )
        )
    ).scalar_one()
    names = await orchestrator_queries.instance_names(session, [iid for iid, _a, _s in gpu_rows])
    items = [
        BillSummaryItem(
            instance_id=iid,
            instance_name=names.get(iid),
            total_amount=as_amount(Decimal(amount or 0)),
            total_seconds=int(secs or 0),
        )
        for iid, amount, secs in gpu_rows
    ]
    return ConsumptionSummary(
        gpu_total=sum((i.total_amount for i in items), Decimal("0.00")),
        disk_total=as_amount(Decimal(disk_total)),
        items=items,
    )


async def billed_by_instance(session: AsyncSession, start, end) -> dict[int, Decimal]:
    """Sum the hourly bill amounts per instance within [start, end) by hour_start."""
    rows = (
        (
            await session.execute(
                select(BillHourly.instance_id, func.sum(BillHourly.amount))
                .where(BillHourly.hour_start >= start, BillHourly.hour_start < end)
                .group_by(BillHourly.instance_id)
            )
        )
        .tuples()
        .all()
    )
    return dict(rows)


async def balances_by_user(
    session: AsyncSession, user_ids: list[int] | None = None
) -> dict[int, Decimal]:
    """Balances of users with a wallet; user_ids limits to the given users."""
    stmt = select(Wallet.user_id, Wallet.balance)
    if user_ids is not None:
        stmt = stmt.where(Wallet.user_id.in_(user_ids))
    rows = (await session.execute(stmt)).tuples().all()
    return dict(rows)


def net_consumption_entries() -> ColumnElement[bool]:
    """Consumption postings plus hourly bill corrections, not unrelated refund payouts.

    Subscription prepayments retain their separate gross accounting basis.
    """
    return (BalanceLedger.type == "consume") | (
        (BalanceLedger.type == "refund") & (BalanceLedger.ref_type == "bill_hourly")
    )


async def consumed_by_user(
    session: AsyncSession, user_ids: list[int] | None = None
) -> dict[int, Decimal]:
    """Net consumption per user after hourly bill refunds; optionally limit to user_ids."""
    stmt = (
        select(BalanceLedger.user_id, func.coalesce(-func.sum(BalanceLedger.amount), 0))
        .where(net_consumption_entries())
        .group_by(BalanceLedger.user_id)
    )
    if user_ids is not None:
        stmt = stmt.where(BalanceLedger.user_id.in_(user_ids))
    rows = (await session.execute(stmt)).tuples().all()
    return dict(rows)


async def revenue_summary(session: AsyncSession, *, tz_offset_minutes: int = 0) -> dict:
    """Today's / this month's consumption and comparison bases. Local day boundary via tz_offset.

    Metered bills window by bill attribution period (bills_hourly.hour_start /
    bills_daily_disk.day);
    subscription prepayments by payment day (subscriptions.created_at). `*_revenue` is the sum,
    `*_prepaid` is listed separately.
    """
    offset = timedelta(minutes=tz_offset_minutes)
    local_now = now_utc() + offset
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0) - offset
    month_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - offset
    prev_day_start = day_start - timedelta(days=1)

    async def _billed_since(start, end=None) -> Decimal:
        hourly_stmt = select(func.coalesce(func.sum(BillHourly.amount), 0)).where(
            BillHourly.hour_start >= start
        )
        daily_stmt = select(func.coalesce(func.sum(BillDailyDisk.amount), 0)).where(
            BillDailyDisk.day >= start
        )
        if end is not None:
            hourly_stmt = hourly_stmt.where(BillHourly.hour_start < end)
            daily_stmt = daily_stmt.where(BillDailyDisk.day < end)
        hourly = (await session.execute(hourly_stmt)).scalar_one()
        daily = (await session.execute(daily_stmt)).scalar_one()
        return Decimal(hourly) + Decimal(daily)

    async def _prepaid_since(start, end=None) -> Decimal:
        stmt = select(func.coalesce(func.sum(Subscription.amount_paid), 0)).where(
            Subscription.created_at >= start
        )
        if end is not None:
            stmt = stmt.where(Subscription.created_at < end)
        return Decimal((await session.execute(stmt)).scalar_one())

    today_prepaid = await _prepaid_since(day_start)
    month_prepaid = await _prepaid_since(month_start)
    return {
        "today_revenue": money_str(await _billed_since(day_start) + today_prepaid),
        "yesterday_revenue": money_str(
            await _billed_since(prev_day_start, day_start)
            + await _prepaid_since(prev_day_start, day_start)
        ),
        "month_revenue": money_str(await _billed_since(month_start) + month_prepaid),
        "today_prepaid": money_str(today_prepaid),
        "month_prepaid": money_str(month_prepaid),
    }


def admin_orders_query(
    *,
    status: str | None = None,
    order_no: str | None = None,
    user_id: int | None = None,
    day_range: tuple[datetime, datetime] | None = None,
) -> Select[tuple[Order]]:
    """Admin top-up order filters (shared by list and CSV): status exact, order_no exact, user_id,
    day_range is the [start, end) created_at window."""
    stmt = select(Order)
    if status:
        stmt = stmt.where(Order.status == status)
    if order_no:
        stmt = stmt.where(Order.order_no == order_no.strip())
    if user_id:
        stmt = stmt.where(Order.user_id == user_id)
    if day_range is not None:
        stmt = stmt.where(Order.created_at >= day_range[0], Order.created_at < day_range[1])
    return stmt


async def admin_list_orders(
    session: AsyncSession,
    status: str | None = None,
    *,
    order_no: str | None = None,
    user_id: int | None = None,
    day_range: tuple[datetime, datetime] | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> RawPage[Order]:
    """Top-up order list (cursor pagination, descending). order_no exact; day_range filters by
    created_at."""
    stmt = admin_orders_query(
        status=status, order_no=order_no, user_id=user_id, day_range=day_range
    ).order_by(Order.id.desc())
    page_items, next_cursor = await paginate_by_id(
        session, stmt, id_col=Order.id, cursor=cursor, limit=limit
    )
    return RawPage(items=page_items, next_cursor=next_cursor)
