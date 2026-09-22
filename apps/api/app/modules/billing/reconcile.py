"""Verify the wallet ledger chain, bills vs prepaid consumption and consume references; money
records are never changed.

Updates reconcile_checkpoints; discrepancies go to logs, metrics and an admin notification.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ColumnElement, SQLColumnExpression, String, cast, func, select, true
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.locks import LockKey, advisory_lock
from app.core.logging import get_logger
from app.core.metrics import FUND_RECONCILE_MISMATCH_TOTAL
from app.core.servercopy import copy as server_copy
from app.core.sqlutil import sum_decimal, total
from app.core.timeutil import billing_day_floor, billing_day_shift, now_utc
from app.modules.billing.models import (
    BalanceLedger,
    BillDailyDisk,
    BillHourly,
    ReconcileCheckpoint,
    Subscription,
    Wallet,
)
from app.modules.billing.wallet import net_consumption_entries
from app.modules.notify import service as notify_service

logger = get_logger(__name__)


@dataclass
class WalletMismatch:
    """Wallet discrepancy. kind: chain_break (row-by-row break) / balance_drift (end snapshot ≠
    balance)."""

    user_id: int
    kind: str
    wallet_balance: Decimal
    expected: Decimal
    detail: str


async def _scan_user_chain(
    session: AsyncSession, wallet_row: Wallet, checkpoint: ReconcileCheckpoint | None
) -> tuple[WalletMismatch | None, int, Decimal]:
    """Scan one user's ledger rows after the checkpoint and verify the chain row by row: by id,
    e.balance_after == previous + e.amount
    (starting from 0.00 without a checkpoint). Returns (discrepancy|None, new cursor
    last_ledger_id, new cursor balance_after).
    """
    last_id = checkpoint.last_ledger_id if checkpoint else 0
    prev = checkpoint.balance_after if checkpoint else Decimal("0.00")
    entries = list(
        (
            await session.execute(
                select(BalanceLedger)
                .where(BalanceLedger.user_id == wallet_row.user_id, BalanceLedger.id > last_id)
                .order_by(BalanceLedger.id)
            )
        ).scalars()
    )
    for e in entries:
        want = prev + e.amount
        if e.balance_after != want:
            return (
                WalletMismatch(
                    user_id=wallet_row.user_id,
                    kind="chain_break",
                    wallet_balance=wallet_row.balance,
                    expected=want,
                    detail=(
                        f"chain break at ledger id={e.id}: balance_after={e.balance_after},"
                        f" expected {want} (previous snapshot {prev} + this amount {e.amount})"
                    ),
                ),
                last_id,
                prev,
            )
        prev = e.balance_after
    if prev != wallet_row.balance:
        return (
            WalletMismatch(
                user_id=wallet_row.user_id,
                kind="balance_drift",
                wallet_balance=wallet_row.balance,
                expected=prev,
                detail=(
                    f"wallet balance {wallet_row.balance} != ledger chain end snapshot {prev}"
                    f" (ledger up to id={entries[-1].id if entries else last_id})"
                ),
            ),
            last_id,
            prev,
        )
    return None, (entries[-1].id if entries else last_id), prev


async def _verify_user_once(
    sm: async_sessionmaker[AsyncSession], user_id: int
) -> WalletMismatch | None:
    """Verify once; consistent → advance the cursor (same transaction), a discrepancy leaves the
    cursor alone."""
    async with sm() as session:
        t0 = (await session.execute(select(func.now()))).scalar_one()
        wallet_row = (
            await session.execute(select(Wallet).where(Wallet.user_id == user_id))
        ).scalar_one()
        checkpoint = await session.get(ReconcileCheckpoint, user_id)
        if checkpoint is not None:
            boundary = await session.get(BalanceLedger, checkpoint.last_ledger_id)
            if (
                boundary is None
                or boundary.user_id != user_id
                or boundary.balance_after != checkpoint.balance_after
            ):
                return WalletMismatch(
                    user_id=user_id,
                    kind="chain_break",
                    wallet_balance=wallet_row.balance,
                    expected=checkpoint.balance_after,
                    detail=(
                        f"cursor row ledger id={checkpoint.last_ledger_id} missing or changed, the"
                        " chain check cannot continue"
                    ),
                )
        mismatch, new_last_id, new_prev = await _scan_user_chain(session, wallet_row, checkpoint)
        if mismatch is not None:
            return mismatch
        await session.execute(
            pg_insert(ReconcileCheckpoint)
            .values(
                user_id=user_id,
                last_ledger_id=new_last_id,
                balance_after=new_prev,
                updated_at=t0,
            )
            .on_conflict_do_update(
                index_elements=["user_id"],
                set_={
                    "last_ledger_id": new_last_id,
                    "balance_after": new_prev,
                    "updated_at": t0,
                },
            )
        )
        await session.commit()
        return None


async def _candidate_user_ids(session: AsyncSession) -> list[int]:
    """Users to verify this round: wallets never verified, or users whose wallet / ledger tail
    changed after the cursor.
    The ledger tail is the max-id row per user via LATERAL: tail id above the cursor / below or
    missing / balance_after
    mismatching all select the user.
    """
    tail = (
        select(
            BalanceLedger.id.label("tail_id"),
            BalanceLedger.balance_after.label("tail_after"),
        )
        .where(BalanceLedger.user_id == ReconcileCheckpoint.user_id)
        .order_by(BalanceLedger.id.desc())
        .limit(1)
        .lateral()
    )
    fresh = (
        select(Wallet.user_id)
        .outerjoin(ReconcileCheckpoint, ReconcileCheckpoint.user_id == Wallet.user_id)
        .where(ReconcileCheckpoint.user_id.is_(None))
    )
    stale = (
        select(ReconcileCheckpoint.user_id)
        .join(Wallet, Wallet.user_id == ReconcileCheckpoint.user_id)
        .outerjoin(tail, true())
        .where(
            (Wallet.updated_at > ReconcileCheckpoint.updated_at)
            | (tail.c.tail_id.is_(None))
            | (tail.c.tail_id != ReconcileCheckpoint.last_ledger_id)
            | (tail.c.tail_after != ReconcileCheckpoint.balance_after)
        )
    )
    return list((await session.execute(fresh.union(stale))).scalars())


async def wallet_ledger_chain_check(sm: async_sessionmaker[AsyncSession]) -> list[WalletMismatch]:
    """Incremental chain verification: scans only the candidate users' rows after the cursor. Users
    failing the first round are re-checked on a fresh session; only two failures are reported."""
    async with sm() as session:
        candidates = await _candidate_user_ids(session)
    mismatches: list[WalletMismatch] = []
    for user_id in candidates:
        mismatch = await _verify_user_once(sm, user_id)
        if mismatch is None:
            continue
        mismatch = await _verify_user_once(sm, user_id)
        if mismatch is not None:
            mismatches.append(mismatch)
    return mismatches


@dataclass(frozen=True)
class _BillSource:
    """One bill source and how it links back to balance_ledger: ledger.ref_type / ref_id = bill
    primary key."""

    ref_type: str
    table: type[BillHourly] | type[BillDailyDisk] | type[Subscription]
    amount_col: SQLColumnExpression[Decimal]
    period_col: SQLColumnExpression[datetime]


_BILL_SOURCES: tuple[_BillSource, ...] = (
    _BillSource("bill_hourly", BillHourly, BillHourly.amount, BillHourly.hour_start),
    _BillSource("bill_daily_disk", BillDailyDisk, BillDailyDisk.amount, BillDailyDisk.day),
    _BillSource("subscription", Subscription, Subscription.amount_paid, Subscription.created_at),
)


async def bills_vs_consume(
    session: AsyncSession, since: datetime, until: datetime
) -> tuple[Decimal, Decimal]:
    """Return bills and linked net consumption for [since, until); the two must be equal.

    Hourly bills window by hour_start, disk fees by day, subscriptions by created_at; the ledger is
    joined by ref_id. Hourly refund credits offset consumption in the original bill's window,
    regardless of posting date. Other refunds do not change the gross prepaid/disk basis.
    """
    billed = consumed = Decimal("0.00")
    for src in _BILL_SOURCES:
        in_window: tuple[ColumnElement[bool], ...] = (
            src.period_col >= since,
            src.period_col < until,
        )
        billed += await sum_decimal(session, select(total(src.amount_col)).where(*in_window))
        consumed -= await sum_decimal(
            session,
            select(total(BalanceLedger.amount))
            .where(net_consumption_entries(), BalanceLedger.ref_type == src.ref_type)
            .join(src.table, BalanceLedger.ref_id == cast(src.table.id, String))
            .where(*in_window),
        )
    return billed, consumed


async def dangling_consume_refs(session: AsyncSession) -> int:
    """Count consumption and hourly correction rows whose ref_id links to no bill."""
    total = 0
    for src in _BILL_SOURCES:
        total += int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(BalanceLedger)
                    .where(
                        net_consumption_entries(),
                        BalanceLedger.ref_type == src.ref_type,
                        ~select(src.table.id)
                        .where(cast(src.table.id, String) == BalanceLedger.ref_id)
                        .exists(),
                    )
                )
            ).scalar_one()
        )
    return total


async def reconcile_funds(
    sm: async_sessionmaker[AsyncSession], *, at: datetime | None = None
) -> dict[str, int]:
    """Under the advisory lock verify the wallet chains, the previous billing day's bills and the
    consume references; returns the wallet discrepancy count and the bill mismatch flag."""
    counts = {"wallet_mismatch": 0, "bill_mismatch": 0}
    async with advisory_lock(sm, LockKey.FUND_RECONCILE) as got:
        if not got:
            return counts
        until = billing_day_floor(at or now_utc())
        since = billing_day_shift(until, -1)
        mismatches = await wallet_ledger_chain_check(sm)
        async with sm() as session:
            billed, consumed = await bills_vs_consume(session, since, until)
            dangling = await dangling_consume_refs(session)

        for m in mismatches:
            counts["wallet_mismatch"] += 1
            FUND_RECONCILE_MISMATCH_TOTAL.labels(kind="wallet_ledger").inc()
            logger.error(
                "fund_reconcile_wallet_mismatch",
                user_id=m.user_id,
                kind=m.kind,
                wallet_balance=str(m.wallet_balance),
                expected=str(m.expected),
                detail=m.detail,
            )
        if billed != consumed or dangling:
            counts["bill_mismatch"] = 1
            FUND_RECONCILE_MISMATCH_TOTAL.labels(kind="bill_consume").inc()
            logger.error(
                "fund_reconcile_bill_mismatch",
                day=since.isoformat(),
                billed=str(billed),
                consumed=str(consumed),
                diff=str(billed - consumed),
                dangling_consume_refs=dangling,
            )

        if counts["wallet_mismatch"] or counts["bill_mismatch"]:
            await _raise_admin_alert(sm, counts, since, billed, consumed)
        else:
            logger.info("fund_reconcile_ok", day=since.isoformat(), billed=str(billed))
    return counts


async def _raise_admin_alert(
    sm: async_sessionmaker[AsyncSession],
    counts: dict[str, int],
    day: datetime,
    billed: Decimal,
    consumed: Decimal,
) -> None:
    parts = []
    if counts["wallet_mismatch"]:
        parts.append(server_copy("billing.reconcile.wallet_part", count=counts["wallet_mismatch"]))
    if counts["bill_mismatch"]:
        parts.append(server_copy("billing.reconcile.bill_part", billed=billed, consumed=consumed))
    async with sm() as session:
        await notify_service.notify(
            session,
            None,
            type_="admin_alert",
            title=server_copy("billing.reconcile.title"),
            content=server_copy("billing.reconcile.content", parts="; ".join(parts)),
            severity="critical",
            dedup_key=f"fund_reconcile:{day.date()}",
        )
        await session.commit()
