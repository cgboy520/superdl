"""充值订单与回调入账。回调幂等三重保障:channel_txn_id 唯一、订单状态检查、行锁。"""

import secrets
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError, ErrorCode, not_found
from app.core.logging import get_logger
from app.core.metrics import (
    PAYMENT_CALLBACK_MISMATCH_TOTAL,
    PAYMENT_LOST_CALLBACK_RECOVERED_TOTAL,
)
from app.core.money import as_amount
from app.core.platform_config import get_effective_platform_config
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

    cfg = await get_effective_platform_config(session)
    if channel_name in ("wechat", "alipay") and cfg[f"payment_{channel_name}_enabled"] != "true":
        raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, "该支付渠道暂未开通,请选择其他支付方式")
    channel = await get_channel(channel_name, session)
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
        PAYMENT_CALLBACK_MISMATCH_TOTAL.inc()
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


async def reconcile_pending_orders(sm: async_sessionmaker[AsyncSession]) -> int:
    """查单收敛(定时任务,每 2 分钟):对 pending 超 60s 的订单主动向渠道查单,
    渠道侧已支付则按回调同路径入账 —— 回调丢失不再等于用户钱丢。

    advisory lock 防多副本重复;单轮 cap 50;渠道不可达跳过该单,下轮再试。
    """
    from app.core.locks import LockKey, try_advisory_lock

    credited = 0
    async with (
        sm() as lock_session,
        try_advisory_lock(lock_session, LockKey.PAYMENT_RECONCILE) as got,
    ):
        if not got:
            return 0
        async with sm() as session:
            orders = list(
                (
                    await session.execute(
                        select(Order)
                        .where(
                            Order.status == "pending",
                            Order.created_at < now_utc() - timedelta(seconds=60),
                        )
                        .order_by(Order.id)
                        .limit(50)
                    )
                ).scalars()
            )
        for order in orders:
            try:
                async with sm() as cfg_session:
                    channel = await get_channel(order.channel, cfg_session)
                result = await channel.query_order(order)
            except AppError as exc:
                logger.warning("order_query_failed", order_no=order.order_no, error=exc.message)
                continue
            if result.status == "paid" and result.channel_txn_id and result.amount is not None:
                async with sm() as session:
                    await handle_callback(
                        session,
                        order.channel,
                        CallbackResult(order.order_no, result.channel_txn_id, result.amount, True),
                    )
                credited += 1
                logger.info("lost_callback_recovered", order_no=order.order_no)
                PAYMENT_LOST_CALLBACK_RECOVERED_TOTAL.inc()
    return credited


async def verify_order(session: AsyncSession, order_no: str) -> dict:
    """管理端:向渠道核验订单(补单前置)。渠道结果是唯一事实源。"""
    order = (
        await session.execute(select(Order).where(Order.order_no == order_no))
    ).scalar_one_or_none()
    if order is None:
        raise AppError(ErrorCode.ORDER_NOT_FOUND, "订单不存在", http_status=404)
    channel = await get_channel(order.channel, session)
    result = await channel.query_order(order)
    matches = (
        result.status == "paid"
        and result.amount is not None
        and as_amount(result.amount) == order.amount
    )
    return {
        "order_no": order.order_no,
        "order_status": order.status,
        "order_amount": str(order.amount),
        "channel_status": result.status,
        "channel_txn_id": result.channel_txn_id,
        "channel_amount": str(result.amount) if result.amount is not None else None,
        "matches": matches,
    }


async def backfill_order(session: AsyncSession, order_no: str) -> Order:
    """管理端人工补单:仅当渠道侧核验为已支付且金额一致才入账(操作者无法凭空造账)。

    幂等由 channel_txn_id 唯一约束 + 行锁 + 状态检查三重保障;closed 订单同样可救。
    """
    order = (
        await session.execute(select(Order).where(Order.order_no == order_no).with_for_update())
    ).scalar_one_or_none()
    if order is None:
        raise AppError(ErrorCode.ORDER_NOT_FOUND, "订单不存在", http_status=404)
    if order.status == "paid":
        raise AppError(ErrorCode.CONFLICT, "订单已入账,无需补单")
    if order.status not in ("pending", "closed"):
        raise AppError(ErrorCode.CONFLICT, f"订单状态 {order.status} 不可补单")
    channel = await get_channel(order.channel, session)
    result = await channel.query_order(order)
    if result.status != "paid" or not result.channel_txn_id or result.amount is None:
        raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, f"渠道侧状态为 {result.status},不能补单")
    if as_amount(result.amount) != order.amount:
        raise AppError(
            ErrorCode.PAYMENT_CHANNEL_ERROR,
            f"渠道金额 {result.amount} 与订单金额 {order.amount} 不符,请走调账",
        )
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
        remark=f"{order.channel} 充值(人工补单)",
    )
    await session.commit()
    logger.info("order_backfilled", order_no=order.order_no, amount=str(order.amount))
    return order


async def list_payment_anomalies(session: AsyncSession) -> list[dict]:
    """异常清单:疑似丢回调(pending 超 10 分钟)、近 48h 被关单、负余额钱包。"""
    from app.modules.billing.models import Wallet

    now = now_utc()
    items: list[dict] = []
    stale = (
        await session.execute(
            select(Order)
            .where(Order.status == "pending", Order.created_at < now - timedelta(minutes=10))
            .order_by(Order.id.desc())
            .limit(100)
        )
    ).scalars()
    for o in stale:
        items.append(
            {
                "kind": "lost_callback",
                "order_no": o.order_no,
                "user_id": o.user_id,
                "amount": str(o.amount),
                "detail": f"{o.channel} 渠道 pending 超 10 分钟,疑似回调丢失",
                "created_at": o.created_at,
            }
        )
    closed_recent = (
        await session.execute(
            select(Order)
            .where(Order.status == "closed", Order.expires_at > now - timedelta(hours=48))
            .order_by(Order.id.desc())
            .limit(100)
        )
    ).scalars()
    for o in closed_recent:
        items.append(
            {
                "kind": "closed_order",
                "order_no": o.order_no,
                "user_id": o.user_id,
                "amount": str(o.amount),
                "detail": "订单超时关闭;若用户声称已付,先核验渠道再补单",
                "created_at": o.created_at,
            }
        )
    negative = (
        await session.execute(select(Wallet).where(Wallet.balance < 0).limit(100))
    ).scalars()
    for w in negative:
        items.append(
            {
                "kind": "negative_balance",
                "order_no": None,
                "user_id": w.user_id,
                "amount": str(w.balance),
                "detail": "钱包负余额(欠费回收后残留),可调账核销",
                "created_at": w.updated_at,
            }
        )
    return items


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
