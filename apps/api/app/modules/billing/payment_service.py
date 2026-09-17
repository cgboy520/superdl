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
from app.core.money import as_amount, platform_currency
from app.core.platform_config import ConfigWarning, RuntimeConfig, get_runtime_config
from app.core.timeutil import now_utc
from app.modules.billing import wallet
from app.modules.billing.models import Order, Wallet
from app.modules.billing.payment_channels import (
    CHANNELS,
    CallbackResult,
    ChannelSpec,
    PaymentChannel,
    Presentation,
    QueryResult,
    channel_error,
    enabled_channels,
    get_channel,
)
from app.modules.billing.schemas import RechargeOut

logger = get_logger(__name__)

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
    """提交充值单后获取支付码,返回 (订单, created);调用方校验金额上下限。

    幂等重放 created=False,待支付且缺支付码时补取。
    """
    amount = as_amount(amount)
    cfg = await get_runtime_config(session)
    spec = _spec(channel_name)
    if not spec.enabled(cfg, payment_mock=get_settings().payment_mock):
        raise channel_error("billing.mockDevOnly" if spec.dev_only else "billing.channelNotEnabled")
    if spec.currencies is not None and platform_currency() not in spec.currencies:
        raise channel_error("billing.channelCurrencyUnsupported")
    channel = await get_channel(channel_name, session)

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
            if existing.status == "pending" and not existing.payment_url:
                return await _attach_payment(session, existing, channel), False
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
    result = await insert_idempotent(
        session,
        order,
        model=Order,
        owner_col=Order.user_id,
        owner_id=user_id,
        key=idempotency_key,
        fingerprint=fingerprint,
        commit=True,
    )
    if result is not order:
        if result.status == "pending" and not result.payment_url:
            return await _attach_payment(session, result, channel), False
        return result, False
    logger.info("recharge_order_created", order_no=order.order_no, user_id=user_id)
    return await _attach_payment(session, order, channel), True


def _spec(channel_name: str) -> ChannelSpec:
    spec = CHANNELS.get(channel_name)
    if spec is None:
        raise AppError(
            ErrorCode.VALIDATION_ERROR, key="billing.unknownChannel", params={"name": channel_name}
        )
    return spec


def _return_urls(order_no: str) -> tuple[str, str]:
    """Where a redirect channel sends the payer back: the billing page resumes polling this order;
    `cancelled=1` shows a notice instead."""
    base = f"{get_settings().web_base_url.rstrip('/')}/billing?recharge={order_no}"
    return base, f"{base}&cancelled=1"


def presentation_of(order: Order) -> Presentation:
    """How the console presents the order's payment (`qr` for unknown/legacy channels)."""
    spec = CHANNELS.get(order.channel)
    return spec.presentation if spec is not None else "qr"


def to_recharge_out(order: Order) -> RechargeOut:
    return RechargeOut(
        order_no=order.order_no,
        amount=order.amount,
        currency=order.currency,
        channel=order.channel,
        presentation=presentation_of(order),
        status=order.status,
        payment_url=order.payment_url,
        expires_at=order.expires_at,
        created_at=order.created_at,
    )


def enabled_payment_channels(cfg: RuntimeConfig) -> list[ChannelSpec]:
    """Channels users may pick now (`/site-config`); the mock channel follows `payment_mock`."""
    return enabled_channels(cfg, payment_mock=get_settings().payment_mock)


def payment_config_warnings(cfg: RuntimeConfig, environment: str) -> list[ConfigWarning]:
    """Enabled channels that cannot settle the platform currency (error), Stripe enabled without
    both credentials (error) and a Stripe test key in prod (warning)."""
    currency = platform_currency()
    out = [
        ConfigWarning(
            spec.enabled_key,
            "error",
            f"{spec.name} settles only in {', '.join(sorted(spec.currencies))}; "
            f"the platform currency is {currency}, so every order on it will be refused",
        )
        for spec in enabled_payment_channels(cfg)
        if spec.enabled_key and spec.currencies is not None and currency not in spec.currencies
    ]
    if cfg.payment_stripe_enabled:
        if not (cfg.stripe_secret_key and cfg.stripe_webhook_secret):
            out.append(
                ConfigWarning(
                    "payment_stripe_enabled",
                    "error",
                    "Stripe is enabled but the secret key or webhook secret is missing: every "
                    "Stripe order and callback will fail",
                )
            )
        elif environment == "prod" and "_test_" in cfg.stripe_secret_key:
            out.append(
                ConfigWarning(
                    "stripe_secret_key",
                    "warning",
                    "Stripe test-mode key in prod: real customers cannot pay through it",
                )
            )
    return out


async def _attach_payment(session: AsyncSession, order: Order, channel: PaymentChannel) -> Order:
    """Ask the channel to start the payment and store its URL / reference; on failure mark the
    order failed, drop the idempotency key, commit and re-raise."""
    return_url, cancel_url = _return_urls(order.order_no)
    try:
        init = await channel.create_payment(order, return_url=return_url, cancel_url=cancel_url)
    except Exception:
        order.status = "failed"
        order.idempotency_key = None
        await session.commit()
        raise
    order.payment_url = init.url
    order.channel_ref = init.channel_ref
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


def _currency_matches(order: Order, reported: str | None) -> bool:
    """A channel that reports no currency is trusted on amount alone."""
    return reported is None or reported == order.currency


def _assert_callback_matches(order: Order, channel_name: str, result: CallbackResult) -> None:
    """Channel, currency and amount must match the order; a mismatch is a channel error."""
    if order.channel != channel_name:
        raise channel_error("billing.callbackChannelMismatch")
    if not _currency_matches(order, result.currency):
        logger.error(
            "callback_currency_mismatch",
            order_no=order.order_no,
            expected=order.currency,
            got=result.currency,
        )
        PAYMENT_CALLBACK_MISMATCH_TOTAL.inc()
        raise channel_error("billing.currencyMismatch")
    if as_amount(result.amount) != order.amount:
        logger.error(
            "callback_amount_mismatch",
            order_no=order.order_no,
            expected=str(order.amount),
            got=str(result.amount),
        )
        PAYMENT_CALLBACK_MISMATCH_TOTAL.inc()
        raise channel_error("billing.callbackAmountMismatch")


async def _order_for_callback(session: AsyncSession, result: CallbackResult) -> Order:
    """Order row (locked) for a callback: by order number, else by the channel transaction id
    (reversals such as card refunds / disputes only carry the latter)."""
    if result.order_no:
        clause = Order.order_no == result.order_no
    elif result.channel_txn_id:
        clause = Order.channel_txn_id == result.channel_txn_id
    else:
        raise channel_error("billing.callbackOrderUnresolved")
    order = (
        await session.execute(select(Order).where(clause).with_for_update())
    ).scalar_one_or_none()
    if order is None:
        raise not_found(key="billing.orderNotFound")
    return order


async def handle_callback(session: AsyncSession, channel_name: str, result: CallbackResult) -> str:
    """持订单行锁处理回调,返回 'ok' 或抛错;调用方须先验签。

    支付成功同事务入账;已支付订单首次反向通知冻结等额余额,冲正通知重放不重复冻结。
    """
    order = await _order_for_callback(session, result)
    if order.status == "paid":
        if not result.success and order.channel_reversed_at is not None:
            logger.error(
                "channel_reversal_replayed",
                order_no=order.order_no,
                channel=channel_name,
                resolved_action=order.channel_reversal_action,
            )
            PAYMENT_CHANNEL_REVERSED_TOTAL.inc()
        elif not result.success:
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
        return "ok"
    rescued = order.status in ("closed", "failed") and result.success
    if order.status != "pending" and not rescued:
        logger.warning("callback_on_closed_order", order_no=order.order_no, status=order.status)
        return "ok"
    _assert_callback_matches(order, channel_name, result)
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
    """持咨询锁查验创建于 60 秒前至 48 小时内的 pending/failed/closed 订单,最多 50 笔。

    closed 还要求 expires_at 在近 48 小时内;渠道已支付时按回调处理,单笔失败不终止整轮。
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
                logger.warning("order_query_failed", order_no=order.order_no, error=str(exc))
                continue
            if not (
                result.status == "paid" and result.channel_txn_id and result.amount is not None
            ):
                continue
            if not _currency_matches(order, result.currency):
                logger.error(
                    "order_currency_mismatch",
                    order_no=order.order_no,
                    expected=order.currency,
                    got=result.currency,
                )
                continue
            try:
                async with sm() as session:
                    await handle_callback(
                        session,
                        order.channel,
                        CallbackResult(
                            order.order_no,
                            result.channel_txn_id,
                            result.amount,
                            True,
                            currency=result.currency,
                        ),
                    )
            except Exception as exc:
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
        and _currency_matches(order, result.currency)
    )
    return {
        "order_no": order.order_no,
        "order_status": order.status,
        "order_amount": str(order.amount),
        "order_currency": order.currency,
        "channel_status": result.status,
        "channel_txn_id": result.channel_txn_id,
        "channel_amount": str(result.amount) if result.amount is not None else None,
        "channel_currency": result.currency,
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
    返回 (订单, replayed);首次入账在提交前调用可选 audit_writer,与入账同事务。
    """
    order = (
        await session.execute(select(Order).where(Order.order_no == order_no))
    ).scalar_one_or_none()
    if order is None:
        raise AppError(ErrorCode.ORDER_NOT_FOUND, key="billing.orderNotFound", http_status=404)
    if _is_backfill_replay(order, idempotency_key):
        return order, True
    channel = await get_channel(order.channel, session)
    result = await _query_with_timeout(channel, order)
    if result.status != "paid" or not result.channel_txn_id or result.amount is None:
        raise AppError(
            ErrorCode.PAYMENT_CHANNEL_ERROR,
            key="billing.channelStateNotBackfillable",
            params={"status": result.status},
        )
    if not _currency_matches(order, result.currency):
        raise AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, key="billing.currencyMismatch")
    if as_amount(result.amount) != order.amount:
        raise AppError(
            ErrorCode.PAYMENT_CHANNEL_ERROR,
            key="billing.amountMismatchAdjust",
            params={"channel": str(result.amount), "order": str(order.amount)},
        )
    order = (
        await session.execute(select(Order).where(Order.order_no == order_no).with_for_update())
    ).scalar_one()
    if _is_backfill_replay(order, idempotency_key):
        return order, True
    order.backfill_idempotency_key = idempotency_key
    try:
        await _credit_paid_order(
            session,
            order,
            channel_txn_id=result.channel_txn_id,
            remark=f"{order.channel} 充值(人工补单)",
        )
    except IntegrityError as exc:
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
        await audit_writer(session)
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
    """将 expires_at 已过期的 pending 订单置 closed 并提交,返回更新行数。"""
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
