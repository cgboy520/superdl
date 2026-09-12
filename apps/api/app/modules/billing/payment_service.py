"""充值订单与回调入账。回调幂等三重保障:channel_txn_id 唯一、订单状态检查、行锁。"""

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode, conflict, not_found
from app.core.idempotency import find_replay, insert_idempotent, request_fingerprint
from app.core.locks import LockKey, advisory_lock
from app.core.logging import get_logger
from app.core.metrics import (
    PAYMENT_CALLBACK_MISMATCH_TOTAL,
    PAYMENT_CHANNEL_REVERSED_TOTAL,
    PAYMENT_CLOSED_ORDER_RESCUED_TOTAL,
    PAYMENT_RECOVER_FAILED_TOTAL,
)
from app.core.money import as_amount
from app.core.platform_config import get_runtime_config
from app.core.timeutil import now_utc
from app.modules.billing import wallet
from app.modules.billing.models import Order, Wallet
from app.modules.billing.payment_channels import (
    CallbackResult,
    PaymentChannel,
    QueryResult,
    get_channel,
)

logger = get_logger(__name__)

# 渠道查单显式超时(SDK 走 asyncio.to_thread 且无自带超时)
CHANNEL_QUERY_TIMEOUT_SECONDS = 15.0


async def _query_with_timeout(channel: PaymentChannel, order: Order) -> QueryResult:
    """带显式超时的渠道查单。超时抛 TimeoutError,调用方按「渠道不可达」处理。"""
    return await asyncio.wait_for(channel.query_order(order), timeout=CHANNEL_QUERY_TIMEOUT_SECONDS)


def _gen_order_no() -> str:
    return f"R{now_utc():%Y%m%d%H%M%S}{secrets.token_hex(4)}"


async def create_recharge(
    session: AsyncSession,
    user_id: int,
    amount: Decimal,
    channel_name: str,
    idempotency_key: str | None,
) -> tuple[Order, bool]:
    """创建充值单。返回 (订单, created):created=False = 幂等重放(含补拉支付码),
    路由回 200 + X-Idempotent-Replay。"""
    amount = as_amount(amount)  # 上下限由 RechargeCreate 契约层校验
    cfg = await get_runtime_config(session)
    channel_enabled = {
        "wechat": cfg.payment_wechat_enabled,
        "alipay": cfg.payment_alipay_enabled,
    }
    if channel_name in channel_enabled and not channel_enabled[channel_name]:
        raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, key="billing.channelNotEnabled")
    channel = await get_channel(channel_name, session)

    # 异参检测指纹:同键改了金额/渠道 → 409
    fingerprint = request_fingerprint(user_id, amount, channel_name)
    if idempotency_key:
        existing = await find_replay(
            session,
            Order,
            owner_col=Order.user_id,
            owner_id=user_id,
            key=idempotency_key,
            fingerprint=fingerprint,
        )
        if existing is not None:
            if existing.status == "pending" and not existing.qr_url:
                return await _attach_payment(session, existing, channel), False  # 补拉支付码
            return existing, False

    order = Order(
        order_no=_gen_order_no(),
        user_id=user_id,
        amount=amount,
        channel=channel.name,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        expires_at=now_utc() + timedelta(seconds=get_settings().recharge_order_ttl_seconds),
    )
    # 同 (user_id, idempotency_key) 并发首单由唯一约束兜底,insert_idempotent 回查胜出方
    result = await insert_idempotent(
        session,
        order,
        model=Order,
        owner_col=Order.user_id,
        owner_id=user_id,
        key=idempotency_key,
        fingerprint=fingerprint,
        commit=True,  # 先落单再调渠道
    )
    if result is not order:
        if result.status == "pending" and not result.qr_url:
            return await _attach_payment(session, result, channel), False  # 胜出方补拉
        return result, False
    logger.info("recharge_order_created", order_no=order.order_no, user_id=user_id)
    return await _attach_payment(session, order, channel), True


async def _attach_payment(session: AsyncSession, order: Order, channel: PaymentChannel) -> Order:
    """向渠道下单并回填二维码。不在事务里调渠道。失败的订单让出幂等键,用户按原键重试即开新单。"""
    try:
        qr_url = await channel.create_payment(order)
    except Exception:
        order.status = "failed"
        order.idempotency_key = None
        await session.commit()
        raise
    order.qr_url = qr_url
    await session.commit()
    return order


async def get_order(session: AsyncSession, user_id: int, order_no: str) -> Order:
    order = (
        await session.execute(
            select(Order).where(Order.order_no == order_no, Order.user_id == user_id)
        )
    ).scalar_one_or_none()
    if order is None:
        raise AppError(ErrorCode.ORDER_NOT_FOUND, key="billing.orderNotFound", http_status=404)
    return order


async def _credit_paid_order(
    session: AsyncSession, order: Order, *, channel_txn_id: str, remark: str
) -> None:
    """订单置 paid + 钱包入账(同事务,不 commit;回调与人工补单共用)。
    先 flush,让 channel_txn_id / backfill_idempotency_key 唯一约束冲突在此显式抛 IntegrityError。
    """
    order.status = "paid"
    order.channel_txn_id = channel_txn_id
    order.paid_at = now_utc()
    await session.flush()
    await wallet.credit(
        session,
        order.user_id,
        order.amount,
        type_="recharge",
        ref_type="order",
        ref_id=order.order_no,
        remark=remark,
    )


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
        if not result.success and order.channel_reversed_at is not None:
            # 同一冲正的重放(含人工处置之后):不再冻结,只留痕 + 计数(PaymentChannelReversed 告警)
            logger.error(
                "channel_reversal_replayed",
                order_no=order.order_no,
                channel=channel_name,
                resolved_action=order.channel_reversal_action,
            )
            PAYMENT_CHANNEL_REVERSED_TOTAL.inc()
        elif not result.success:
            # 渠道侧对已入账订单的关单/退款通知:不自动冲账,等额冻结钱包;解冻/扣回走管理端
            # /finance/reversals/{order_no}/resolve。幂等:只对首次置标的那一次冻结
            order.channel_reversed_at = now_utc()
            await wallet.freeze(
                session,
                order.user_id,
                order.amount,
                ref_id=order.order_no,
                remark="渠道冲正冻结",
            )
            await session.commit()
            logger.error(
                "channel_reversed_on_paid_order",
                order_no=order.order_no,
                channel=channel_name,
                channel_txn_id=result.channel_txn_id,
                frozen=str(order.amount),
                refund_amount=str(result.refund_amount) if result.refund_amount else None,
            )
            PAYMENT_CHANNEL_REVERSED_TOTAL.inc()
        return "ok"  # 重放
    # 关单/失败单后到达的有效成功回调:按人工补单同等校验(渠道一致+金额一致)自动入账
    rescued = order.status in ("closed", "failed") and result.success
    if order.status != "pending" and not rescued:
        logger.warning("callback_on_closed_order", order_no=order.order_no, status=order.status)
        return "ok"
    if order.channel != channel_name:
        raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, key="billing.callbackChannelMismatch")
    if as_amount(result.amount) != order.amount:
        logger.error(
            "callback_amount_mismatch",
            order_no=order.order_no,
            expected=str(order.amount),
            got=str(result.amount),
        )
        PAYMENT_CALLBACK_MISMATCH_TOTAL.inc()
        raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, key="billing.callbackAmountMismatch")
    if not result.success:
        order.status = "failed"
        await session.commit()
        return "ok"

    await _credit_paid_order(
        session, order, channel_txn_id=result.channel_txn_id, remark=f"{channel_name} 充值"
    )
    await session.commit()
    if rescued:
        logger.info("closed_order_auto_credited", order_no=order.order_no)
        PAYMENT_CLOSED_ORDER_RESCUED_TOTAL.inc()
    logger.info("recharge_paid", order_no=order.order_no, amount=str(order.amount))
    return "ok"


async def reconcile_pending_orders(sm: async_sessionmaker[AsyncSession]) -> int:
    """查单收敛(定时任务,每 2 分钟):对 pending 超 60s、近 48h failed 及近 48h closed 的订单
    主动向渠道查单,渠道侧已支付则按回调同路径入账。closed 单以 expires_at 界定 48h 窗口。

    advisory lock 防多副本重复;单轮 cap 50;渠道不可达跳过该单,下轮再试。
    """
    credited = 0
    async with advisory_lock(sm, LockKey.PAYMENT_RECONCILE) as got:
        if not got:
            return 0
        async with sm() as session:
            orders = list(
                (
                    await session.execute(
                        select(Order)
                        .where(
                            or_(
                                Order.status.in_(("pending", "failed")),
                                # closed 单按关单时刻(expires_at)限定近 48h
                                Order.status == "closed",
                            ),
                            Order.created_at < now_utc() - timedelta(seconds=60),
                            Order.created_at > now_utc() - timedelta(hours=48),
                            or_(
                                Order.status != "closed",
                                Order.expires_at > now_utc() - timedelta(hours=48),
                            ),
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
                result = await _query_with_timeout(channel, order)
            except Exception as exc:
                # 渠道不可达/查单超时:跳过该单,下轮再试
                logger.warning("order_query_failed", order_no=order.order_no, error=str(exc))
                continue
            if not (
                result.status == "paid" and result.channel_txn_id and result.amount is not None
            ):
                continue
            try:
                async with sm() as session:
                    await handle_callback(
                        session,
                        order.channel,
                        CallbackResult(order.order_no, result.channel_txn_id, result.amount, True),
                    )
            except Exception as exc:
                # 单笔入账失败记日志+指标,不中断整轮
                logger.exception("order_recover_failed", order_no=order.order_no)
                PAYMENT_RECOVER_FAILED_TOTAL.labels(error=type(exc).__name__).inc()
                continue
            credited += 1
            logger.info("lost_callback_recovered", order_no=order.order_no)
    return credited


async def verify_order(session: AsyncSession, order_no: str) -> dict:
    """管理端:向渠道核验订单(补单前置)。"""
    order = (
        await session.execute(select(Order).where(Order.order_no == order_no))
    ).scalar_one_or_none()
    if order is None:
        raise AppError(ErrorCode.ORDER_NOT_FOUND, key="billing.orderNotFound", http_status=404)
    channel = await get_channel(order.channel, session)
    result = await _query_with_timeout(channel, order)
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


def _is_backfill_replay(order: Order, idempotency_key: str | None) -> bool:
    """补单状态检查(锁外预检与锁内复核同一段):已入账且同键 → True(幂等重放);
    已入账异键 → 409;不可补的状态 → 409;可补 → False。"""
    if order.status == "paid":
        if idempotency_key is not None and order.backfill_idempotency_key == idempotency_key:
            return True
        raise conflict(key="billing.orderAlreadyPaid")
    if order.status not in ("pending", "closed", "failed"):
        raise conflict(key="billing.orderStateNotBackfillable", params={"status": order.status})
    return False


async def backfill_order(
    session: AsyncSession,
    order_no: str,
    idempotency_key: str | None = None,
    audit_writer: Callable[[AsyncSession], Awaitable[None]] | None = None,
) -> tuple[Order, bool]:
    """管理端人工补单:仅当渠道侧核验为已支付且金额一致才入账,closed/failed 订单同样可补。

    幂等:channel_txn_id 唯一约束 + 行锁 + 状态检查 + backfill_idempotency_key 唯一约束。
    渠道查单在无锁状态下进行(带显式超时),落账前在锁内复核订单状态。
    返回 (订单, replayed):replayed=True 表示同键重放,路由据此回 X-Idempotent-Replay 头。
    """
    order = (
        await session.execute(select(Order).where(Order.order_no == order_no))
    ).scalar_one_or_none()
    if order is None:
        raise AppError(ErrorCode.ORDER_NOT_FOUND, key="billing.orderNotFound", http_status=404)
    if _is_backfill_replay(order, idempotency_key):
        return order, True  # 同键重放
    channel = await get_channel(order.channel, session)
    result = await _query_with_timeout(channel, order)
    if result.status != "paid" or not result.channel_txn_id or result.amount is None:
        raise AppError(
            ErrorCode.PAYMENT_CHANNEL_ERROR,
            key="billing.channelStateNotBackfillable",
            params={"status": result.status},
        )
    if as_amount(result.amount) != order.amount:
        raise AppError(
            ErrorCode.PAYMENT_CHANNEL_ERROR,
            key="billing.amountMismatchAdjust",
            params={"channel": str(result.amount), "order": str(order.amount)},
        )
    # 锁内复核:查渠道期间订单可能已被回调/并发补单入账
    order = (
        await session.execute(select(Order).where(Order.order_no == order_no).with_for_update())
    ).scalar_one()
    if _is_backfill_replay(order, idempotency_key):
        return order, True  # 并发同键补单已胜出
    order.backfill_idempotency_key = idempotency_key
    try:
        await _credit_paid_order(
            session,
            order,
            channel_txn_id=result.channel_txn_id,
            remark=f"{order.channel} 充值(人工补单)",
        )
    except IntegrityError as exc:
        # backfill_idempotency_key 唯一约束兜底:同键被并发用到另一笔订单,回查持键方后 409
        await session.rollback()
        if idempotency_key is not None:
            holder = (
                await session.execute(
                    select(Order).where(Order.backfill_idempotency_key == idempotency_key)
                )
            ).scalar_one_or_none()
            if holder is not None and holder.order_no != order_no:
                raise conflict(
                    key="billing.backfillKeyInUse", params={"order_no": holder.order_no}
                ) from exc
        raise
    if audit_writer is not None:
        await audit_writer(session)  # 与入账同事务
    await session.commit()
    logger.info("order_backfilled", order_no=order.order_no, amount=str(order.amount))
    return order, False


ANOMALY_LIMIT_PER_KIND = 100


def _order_anomaly_specs(now: datetime) -> list[tuple[str, ColumnElement[bool], str, str]]:
    """订单类异常分桶:(kind, 谓词, detail 模板, 时间列名)。detail 模板可引用 {channel}。"""
    return [
        (
            "lost_callback",
            and_(Order.status == "pending", Order.created_at < now - timedelta(minutes=10)),
            "{channel} 渠道 pending 超 10 分钟,疑似回调丢失",
            "created_at",
        ),
        (
            "closed_order",
            and_(Order.status == "closed", Order.expires_at > now - timedelta(hours=48)),
            "订单超时关闭;若用户声称已付,先核验渠道再补单",
            "created_at",
        ),
        (
            "failed_order",
            and_(Order.status == "failed", Order.created_at > now - timedelta(hours=48)),
            "渠道中间态/失败回调置 failed;查单收敛会自动救回已支付单,亦可人工补单",
            "created_at",
        ),
        (
            "channel_reversed",
            and_(
                Order.channel_reversed_at > now - timedelta(hours=48),
                Order.channel_reversal_resolved_at.is_(None),
            ),
            "已入账订单收到渠道关单/退款通知:钱包已等额冻结阻断消费;"
            "核实后经 /finance/reversals/{{order_no}}/resolve 解冻(噪音单)或扣回(确认反转)",
            "channel_reversed_at",
        ),
    ]


async def list_payment_anomalies(session: AsyncSession) -> list[dict[str, Any]]:
    """异常清单:疑似丢回调(pending 超 10 分钟)、近 48h 被关单、近 48h 失败单、
    未处置的渠道冲正、负余额钱包。每桶最多 ANOMALY_LIMIT_PER_KIND 条。"""
    now = now_utc()
    items: list[dict[str, Any]] = []
    for kind, predicate, detail, ts_attr in _order_anomaly_specs(now):
        rows = (
            await session.execute(
                select(Order)
                .where(predicate)
                .order_by(Order.id.desc())
                .limit(ANOMALY_LIMIT_PER_KIND)
            )
        ).scalars()
        items.extend(
            {
                "kind": kind,
                "order_no": o.order_no,
                "user_id": o.user_id,
                "amount": str(o.amount),
                "detail": detail.format(channel=o.channel),
                "created_at": getattr(o, ts_attr),
            }
            for o in rows
        )
    negative = (
        await session.execute(
            select(Wallet).where(Wallet.balance < 0).limit(ANOMALY_LIMIT_PER_KIND)
        )
    ).scalars()
    items.extend(
        {
            "kind": "negative_balance",
            "order_no": None,
            "user_id": w.user_id,
            "amount": str(w.balance),
            "detail": "钱包负余额(欠费回收后残留),可调账核销",
            "created_at": w.updated_at,
        }
        for w in negative
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
