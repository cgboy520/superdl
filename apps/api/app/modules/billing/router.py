from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.core.db import DbSession
from app.core.errors import AppError, ErrorCode
from app.core.pagination import Page, clamp_limit, decode_cursor_int, encode_cursor
from app.modules.account.deps import CurrentUser
from app.modules.billing import wallet
from app.modules.billing.models import BalanceLedger, BillDailyDisk, BillHourly
from app.modules.billing.schemas import (
    BillHourlyOut,
    BillSummaryItem,
    BillSummaryOut,
    LedgerEntryOut,
    WalletOut,
)

router = APIRouter(tags=["billing"])


def _parse_month(month: str) -> tuple[datetime, datetime]:
    try:
        start = datetime.strptime(month, "%Y-%m").replace(tzinfo=UTC)
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, "月份格式应为 YYYY-MM") from exc
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start, end


@router.get("/wallet")
async def get_wallet(user: CurrentUser, session: DbSession) -> WalletOut:
    w = await wallet.get_or_create_wallet(session, user.id)
    await session.commit()
    return WalletOut.model_validate(w)


@router.get("/wallet/ledger")
async def get_ledger(
    user: CurrentUser,
    session: DbSession,
    cursor: str | None = None,
    limit: int | None = Query(default=None, le=100),
) -> Page[LedgerEntryOut]:
    lim = clamp_limit(limit)
    stmt = (
        select(BalanceLedger)
        .where(BalanceLedger.user_id == user.id)
        .order_by(BalanceLedger.id.desc())
        .limit(lim + 1)
    )
    last_id = decode_cursor_int(cursor)
    if last_id is not None:
        stmt = stmt.where(BalanceLedger.id < last_id)
    rows = list((await session.execute(stmt)).scalars())
    next_cursor = encode_cursor(rows[lim - 1].id) if len(rows) > lim else None
    return Page(
        items=[LedgerEntryOut.model_validate(r) for r in rows[:lim]], next_cursor=next_cursor
    )


@router.get("/bills/hourly")
async def list_hourly_bills(
    user: CurrentUser,
    session: DbSession,
    instance_id: int | None = None,
    month: str | None = None,
    cursor: str | None = None,
    limit: int | None = Query(default=None, le=100),
) -> Page[BillHourlyOut]:
    lim = clamp_limit(limit)
    stmt = (
        select(BillHourly)
        .where(BillHourly.user_id == user.id)
        .order_by(BillHourly.id.desc())
        .limit(lim + 1)
    )
    if instance_id is not None:
        stmt = stmt.where(BillHourly.instance_id == instance_id)
    if month is not None:
        start, end = _parse_month(month)
        stmt = stmt.where(BillHourly.hour_start >= start, BillHourly.hour_start < end)
    last_id = decode_cursor_int(cursor)
    if last_id is not None:
        stmt = stmt.where(BillHourly.id < last_id)
    rows = list((await session.execute(stmt)).scalars())
    next_cursor = encode_cursor(rows[lim - 1].id) if len(rows) > lim else None
    return Page(
        items=[BillHourlyOut.model_validate(r) for r in rows[:lim]], next_cursor=next_cursor
    )


@router.get("/bills/summary")
async def bill_summary(user: CurrentUser, session: DbSession, month: str) -> BillSummaryOut:
    """月度汇总 + 按实例成本归因(消费概览环图数据源)。"""
    start, end = _parse_month(month)
    gpu_rows = (
        (
            await session.execute(
                select(
                    BillHourly.instance_id,
                    func.sum(BillHourly.amount),
                    func.sum(BillHourly.seconds_used),
                )
                .where(
                    BillHourly.user_id == user.id,
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
                BillDailyDisk.user_id == user.id,
                BillDailyDisk.day >= start,
                BillDailyDisk.day < end,
            )
        )
    ).scalar_one()
    items = [
        BillSummaryItem(
            instance_id=iid, total_amount=amount or Decimal("0.00"), total_seconds=int(secs or 0)
        )
        for iid, amount, secs in gpu_rows
    ]
    gpu_total = sum((i.total_amount for i in items), Decimal("0.00"))
    return BillSummaryOut(
        month=month, gpu_total=gpu_total, disk_total=Decimal(disk_total), items=items
    )
