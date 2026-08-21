"""钱包原语:所有余额变动的唯一入口。

铁律:更新必须 `SELECT ... FOR UPDATE`,同事务写 balance_ledger(balance_after 快照)。
本文件函数不 commit —— 由调用方把余额变动放进业务事务。
"""

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.money import as_amount
from app.modules.billing.models import BalanceLedger, Wallet


async def get_or_create_wallet(session: AsyncSession, user_id: int) -> Wallet:
    wallet = (
        await session.execute(select(Wallet).where(Wallet.user_id == user_id))
    ).scalar_one_or_none()
    if wallet is None:
        wallet = Wallet(user_id=user_id)
        session.add(wallet)
        await session.flush()
    return wallet


async def lock_wallet(session: AsyncSession, user_id: int) -> Wallet:
    """FOR UPDATE 锁定钱包行(不存在则先创建)。"""
    wallet = (
        await session.execute(select(Wallet).where(Wallet.user_id == user_id).with_for_update())
    ).scalar_one_or_none()
    if wallet is None:
        session.add(Wallet(user_id=user_id))
        await session.flush()
        wallet = (
            await session.execute(select(Wallet).where(Wallet.user_id == user_id).with_for_update())
        ).scalar_one()
    return wallet


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
    type_: str,  # recharge / refund / adjust
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
    allow_negative: bool = True,
) -> Wallet:
    """扣款。计费扣款允许透支为负(欠费链路负责停机回收);主动消费类不允许。"""
    amount = as_amount(amount)
    if amount <= 0:
        raise ValueError("debit amount must be positive")
    wallet = await lock_wallet(session, user_id)
    new_balance = as_amount(wallet.balance - amount)
    if not allow_negative and new_balance < 0:
        raise AppError(ErrorCode.INSUFFICIENT_BALANCE, key="billing.insufficientBalance")
    wallet.balance = new_balance
    session.add(_ledger(wallet, type_, -amount, ref_type, ref_id, remark))
    return wallet


async def get_balance(session: AsyncSession, user_id: int) -> Decimal:
    wallet = (
        await session.execute(select(Wallet).where(Wallet.user_id == user_id))
    ).scalar_one_or_none()
    return wallet.balance if wallet else Decimal("0.00")


async def require_balance_at_least(
    session: AsyncSession,
    user_id: int,
    amount: Decimal,
    *,
    hint_key: str,
    hint_params: Mapping[str, Any] | None = None,
) -> None:
    """开机前校验:余额 ≥ 预估费用。只读校验,不预占。"""
    balance = await get_balance(session, user_id)
    if balance < as_amount(amount):
        raise AppError(
            ErrorCode.INSUFFICIENT_BALANCE,
            key=hint_key,
            params=hint_params,
            detail={"balance": format(balance, "f"), "required": format(as_amount(amount), "f")},
        )


async def billed_by_instance(session: AsyncSession, start, end) -> dict[int, Decimal]:
    """对账用:窗口内各实例的事件计费合计(bills_hourly)。"""
    from sqlalchemy import func

    from app.modules.billing.models import BillHourly

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


async def balances_by_user(session: AsyncSession) -> dict[int, Decimal]:
    rows = (await session.execute(select(Wallet.user_id, Wallet.balance))).tuples().all()
    return dict(rows)


async def consumed_by_user(session: AsyncSession) -> dict[int, Decimal]:
    """累计消费(ledger consume 合计的绝对值)。"""
    from sqlalchemy import func

    rows = (
        (
            await session.execute(
                select(BalanceLedger.user_id, func.coalesce(-func.sum(BalanceLedger.amount), 0))
                .where(BalanceLedger.type == "consume")
                .group_by(BalanceLedger.user_id)
            )
        )
        .tuples()
        .all()
    )
    return dict(rows)


async def revenue_summary(session: AsyncSession, *, tz_offset_minutes: int = 0) -> dict:
    """今日/本月消费额(ledger consume 绝对值)与环比基数。本地日界按 tz_offset 折算。"""
    from datetime import timedelta

    from sqlalchemy import func

    from app.core.timeutil import now_utc

    offset = timedelta(minutes=tz_offset_minutes)
    local_now = now_utc() + offset
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0) - offset
    month_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - offset
    prev_day_start = day_start - timedelta(days=1)

    async def _consume_since(start, end=None) -> str:
        stmt = select(func.coalesce(-func.sum(BalanceLedger.amount), 0)).where(
            BalanceLedger.type == "consume", BalanceLedger.created_at >= start
        )
        if end is not None:
            stmt = stmt.where(BalanceLedger.created_at < end)
        return format((await session.execute(stmt)).scalar_one(), "f")

    return {
        "today_revenue": await _consume_since(day_start),
        "yesterday_revenue": await _consume_since(prev_day_start, day_start),
        "month_revenue": await _consume_since(month_start),
    }


async def admin_list_orders(session: AsyncSession, status: str | None = None) -> list:
    from app.modules.billing.models import Order

    stmt = select(Order).order_by(Order.id.desc()).limit(200)
    if status:
        stmt = stmt.where(Order.status == status)
    return list((await session.execute(stmt)).scalars())
