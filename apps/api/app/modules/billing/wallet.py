"""钱包原语:所有余额变动的唯一入口。

铁律:更新必须 `SELECT ... FOR UPDATE`,同事务写 balance_ledger(balance_after 快照)。
本文件函数不 commit —— 由调用方把余额变动放进业务事务。
"""

from decimal import Decimal

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
        await get_or_create_wallet(session, user_id)
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
        raise AppError(ErrorCode.INSUFFICIENT_BALANCE, "余额不足,请先充值")
    wallet.balance = new_balance
    session.add(_ledger(wallet, type_, -amount, ref_type, ref_id, remark))
    return wallet


async def get_balance(session: AsyncSession, user_id: int) -> Decimal:
    wallet = (
        await session.execute(select(Wallet).where(Wallet.user_id == user_id))
    ).scalar_one_or_none()
    return wallet.balance if wallet else Decimal("0.00")


async def require_balance_at_least(
    session: AsyncSession, user_id: int, amount: Decimal, *, hint: str
) -> None:
    """开机前校验:余额 ≥ 预估费用。只读校验,不预占。"""
    balance = await get_balance(session, user_id)
    if balance < as_amount(amount):
        raise AppError(
            ErrorCode.INSUFFICIENT_BALANCE,
            f"余额不足:{hint}",
            detail={"balance": format(balance, "f"), "required": format(as_amount(amount), "f")},
        )
