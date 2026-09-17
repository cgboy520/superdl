"""Top-up orders and callback crediting. Callback idempotency has three layers: unique
channel_txn_id, order status check, row lock."""

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
from app.core.money import as_amount, money_label, platform_currency
from app.core.platform_config import ConfigWarning, RuntimeConfig, get_runtime_config
from app.core.servercopy import copy as server_copy
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
    """Channel order query with an explicit timeout. A timeout raises TimeoutError, which the caller
    treats as "channel unreachable"."""
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
    """Submit the top-up order and obtain the payment code, returning (order, created); the caller
    validates the amount bounds.

    An idempotent replay has created=False; a pending order without a payment code fetches one.
    """
    amount = as_amount(amount)
    cfg = await get_runtime_config(session)
    _assert_recharge_amount(cfg, amount)
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


def _assert_recharge_amount(cfg: RuntimeConfig, amount: Decimal) -> None:
    """Business bounds (`recharge_min` / `recharge_max` policies); the schema only caps sanity."""
    if not cfg.recharge_min <= amount <= cfg.recharge_max:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="billing.rechargeAmountOutOfRange",
            params={"min": money_label(cfg.recharge_min), "max": money_label(cfg.recharge_max)},
        )


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
    """Order → paid + wallet credit (same transaction, no commit; shared by callbacks and manual
    backfill).
    Flush first so channel_txn_id / backfill_idempotency_key unique conflicts raise IntegrityError
    here explicitly.
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
    """Handle a callback under the order row lock, returning 'ok' or raising; the caller verifies
    the
    signature first.

    A successful payment credits in the same transaction; the first reversal notice on a paid order
    freezes the same amount, replays do not freeze again.
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
                remark=server_copy("billing.remark.reversal_freeze"),
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
        session,
        order,
        channel_txn_id=result.channel_txn_id,
        remark=server_copy("billing.remark.recharge", channel=channel_name),
    )
    await session.commit()
    if rescued:
        logger.info("closed_order_auto_credited", order_no=order.order_no)
        PAYMENT_CLOSED_ORDER_RESCUED_TOTAL.inc()
    logger.info("recharge_paid", order_no=order.order_no, amount=str(order.amount))
    return "ok"


async def reconcile_pending_orders(sm: async_sessionmaker[AsyncSession]) -> int:
    """Under the advisory lock, verify pending/failed/closed orders created 60 s to 48 h ago, at
    most 50.

    closed additionally requires expires_at within the last 48 h; paid on the channel side is
    handled like a callback, a single failure does not end the round.
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
    """Admin: verify the order with the channel (backfill precondition)."""
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
    """Backfill status check (the same block for the unlocked precheck and the locked re-check):
    credited with the same key → True (idempotent replay);
    credited with another key → 409; a non-backfillable status → 409; backfillable → False."""
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
    """Admin manual backfill: credits only when the channel confirms paid with a matching amount;
    closed/failed orders can be backfilled too.

    Idempotency: unique channel_txn_id + row lock + status check + unique
    backfill_idempotency_key.
    The channel query runs without the lock (explicit timeout); the order status is re-checked
    under the lock before posting.
    Returns (order, replayed); the first credit calls the optional audit_writer before commit, in
    the same transaction.
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
            remark=server_copy("billing.remark.recharge_backfill", channel=order.channel),
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
    """Order anomaly buckets: (kind, predicate, detail copy key, time column). The copy takes
    {channel}."""
    return [
        (
            "lost_callback",
            and_(Order.status == "pending", Order.created_at < now - timedelta(minutes=10)),
            "billing.anomaly.lost_callback",
            "created_at",
        ),
        (
            "closed_order",
            and_(Order.status == "closed", Order.expires_at > now - timedelta(hours=48)),
            "billing.anomaly.closed_order",
            "created_at",
        ),
        (
            "failed_order",
            and_(Order.status == "failed", Order.created_at > now - timedelta(hours=48)),
            "billing.anomaly.failed_order",
            "created_at",
        ),
        (
            "channel_reversed",
            and_(
                Order.channel_reversed_at > now - timedelta(hours=48),
                Order.channel_reversal_resolved_at.is_(None),
            ),
            "billing.anomaly.channel_reversed",
            "channel_reversed_at",
        ),
    ]


async def list_payment_anomalies(session: AsyncSession) -> list[dict[str, Any]]:
    """Anomaly list: suspected lost callbacks (pending > 10 minutes), orders closed in the last
    48 h, failed orders of the last 48 h,
    unhandled channel reversals, negative wallets. At most ANOMALY_LIMIT_PER_KIND per bucket."""
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
                "detail": server_copy(detail, channel=o.channel),
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
            "detail": server_copy("billing.anomaly.negative_balance"),
            "created_at": w.updated_at,
        }
        for w in negative
    )
    return items


async def close_expired_orders(sm: async_sessionmaker[AsyncSession]) -> int:
    """Set expired pending orders to closed and commit, returning the updated row count."""
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
