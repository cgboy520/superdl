"""充值订单与回调入账。回调幂等三重保障:channel_txn_id 唯一、订单状态检查、行锁。"""

import secrets
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError, ErrorCode, not_found
from app.core.logging import get_logger
from app.core.money import as_amount
from app.core.timeutil import now_utc
from app.modules.billing import wallet
from app.modules.billing.models import Order
from app.modules.billing.payment_channels import CallbackResult, get_channel

logger = get_logger(__name__)

MIN_RECHARGE = Decimal("1.00")
MAX_RECHARGE = Decimal("50000.00")


def _gen_order_no() -> str:
    return f"R{now_utc():%Y%m%d%H%M%S}{secrets.token_hex(4)}"


async def create_recharge(
    session: AsyncSession,
    user_id: int,
    amount: Decimal,
    channel_name: str,
    idempotency_key: str | None,
) -> Order:
    from app.core.config import get_settings

    amount = as_amount(amount)
    if not MIN_RECHARGE <= amount <= MAX_RECHARGE:
        raise AppError(
            ErrorCode.VALIDATION_ERROR, f"充值金额须在 {MIN_RECHARGE}~{MAX_RECHARGE} 元之间"
        )
    if idempotency_key:
        existing = (
            await session.execute(
                select(Order).where(
                    Order.user_id == user_id, Order.idempotency_key == idempotency_key
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    channel = get_channel(channel_name)
    order = Order(
        order_no=_gen_order_no(),
        user_id=user_id,
        amount=amount,
        channel=channel.name,
        idempotency_key=idempotency_key,
        expires_at=now_utc() + timedelta(seconds=get_settings().recharge_order_ttl_seconds),
    )
    session.add(order)
    await session.flush()
    order.qr_url = await channel.create_payment(order)
    await session.commit()
    logger.info("recharge_order_created", order_no=order.order_no, user_id=user_id)
    return order


async def get_order(session: AsyncSession, user_id: int, order_no: str) -> Order:
    order = (
        await session.execute(
            select(Order).where(Order.order_no == order_no, Order.user_id == user_id)
        )
    ).scalar_one_or_none()
    if order is None:
        raise AppError(ErrorCode.ORDER_NOT_FOUND, "订单不存在", http_status=404)
    return order


async def handle_callback(session: AsyncSession, channel_name: str, result: CallbackResult) -> str:
    """处理支付回调。返回 'ok'(含重放)或抛错。重放回调不重复入账。"""
    order = (
        await session.execute(
            select(Order).where(Order.order_no == result.order_no).with_for_update()
        )
    ).scalar_one_or_none()
    if order is None:
        raise not_found("订单不存在")
    if order.status == "paid":
        return "ok"  # 重放:已入账,直接确认
    if order.status != "pending":
        logger.warning("callback_on_closed_order", order_no=order.order_no, status=order.status)
        return "ok"
    if order.channel != channel_name:
        raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, "回调渠道与订单不符")
    if as_amount(result.amount) != order.amount:
        logger.error(
            "callback_amount_mismatch",
            order_no=order.order_no,
            expected=str(order.amount),
            got=str(result.amount),
        )
        raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, "回调金额与订单不符")
    if not result.success:
        order.status = "failed"
        await session.commit()
        return "ok"

    # channel_txn_id 唯一约束兜底:同一渠道流水号只可能入账一次
    order.status = "paid"
    order.channel_txn_id = result.channel_txn_id
    order.paid_at = now_utc()
    await wallet.credit(
        session,
        order.user_id,
        order.amount,
        type_="recharge",
        ref_type="order",
        ref_id=order.order_no,
        remark=f"{channel_name} 充值",
    )
    await session.commit()
    logger.info("recharge_paid", order_no=order.order_no, amount=str(order.amount))
    return "ok"


async def close_expired_orders(sm: async_sessionmaker[AsyncSession]) -> int:
    """关闭超时未支付订单(定时任务,每 10 分钟)。"""
    async with sm() as session:
        result = await session.execute(
            update(Order)
            .where(Order.status == "pending", Order.expires_at < now_utc())
            .values(status="closed")
        )
        await session.commit()
        count = result.rowcount  # type: ignore[attr-defined]
        if count:
            logger.info("expired_orders_closed", count=count)
        return count
