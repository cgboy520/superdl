"""Stripe Checkout (hosted page, `redirect` presentation). Orders become Checkout Sessions with the
order number as `client_reference_id` and as PaymentIntent metadata, so every later event (session
completion, async payment results, refunds, disputes) maps back to the order. Refunds stay
off-channel: `charge.refunded` / `charge.dispute.created` only freeze the credited balance."""

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from app.core.currencies import CURRENCY_MINOR_UNITS
from app.core.errors import AppError, ErrorCode
from app.core.platform_config import RuntimeConfig
from app.core.timeutil import now_utc
from app.modules.billing.payment_channels.base import (
    SDK_TIMEOUT,
    CallbackResult,
    PaymentInit,
    QueryResult,
    channel_error,
    header_value,
    run_in_sdk_pool,
)

if TYPE_CHECKING:
    from app.modules.billing.models import Order

STRIPE_CFG_KEYS = ("stripe_secret_key", "stripe_webhook_secret")
#: Stripe requires Checkout Sessions to expire between 30 minutes and 24 hours after creation;
#: the buffer keeps a request that crosses a second boundary above the lower bound.
SESSION_EXPIRY_MIN_SECONDS = 30 * 60
SESSION_EXPIRY_MAX_SECONDS = 24 * 3600
SESSION_EXPIRY_BUFFER_SECONDS = 60
#: Tolerance for the signed timestamp in `Stripe-Signature`.
SIGNATURE_TOLERANCE_SECONDS = 300

_SESSION_PAID_EVENTS = ("checkout.session.completed", "checkout.session.async_payment_succeeded")
_SESSION_FAILED_EVENTS = ("checkout.session.async_payment_failed", "checkout.session.expired")


def to_minor(amount: Decimal, currency: str) -> int:
    """Decimal amount → Stripe integer minor units (JPY 1000 → 1000, USD 50.00 → 5000)."""
    return int(amount.scaleb(CURRENCY_MINOR_UNITS[currency.upper()]))


def from_minor(amount: int, currency: str) -> Decimal:
    exponent = CURRENCY_MINOR_UNITS.get(currency.upper(), 2)
    return Decimal(amount).scaleb(-exponent)


def _clamp_expiry(expires_at_ts: float, now_ts: float) -> int:
    lo = int(now_ts) + SESSION_EXPIRY_MIN_SECONDS + SESSION_EXPIRY_BUFFER_SECONDS
    hi = int(now_ts) + SESSION_EXPIRY_MAX_SECONDS
    return max(lo, min(hi, int(expires_at_ts)))


class StripeChannel:
    """Checkout Session per order; webhook events verified with the endpoint's signing secret."""

    name = "stripe"

    def __init__(self, cfg: RuntimeConfig, *, client: Any | None = None) -> None:
        if not all(getattr(cfg, k) for k in STRIPE_CFG_KEYS):
            raise channel_error("billing.stripeCredentialsIncomplete")
        self._webhook_secret = cfg.stripe_webhook_secret
        if client is None:
            import stripe

            client = stripe.StripeClient(
                cfg.stripe_secret_key, http_client=stripe.RequestsClient(timeout=SDK_TIMEOUT)
            )
        self._client = client

    def session_params(self, order: "Order", *, return_url: str, cancel_url: str) -> dict[str, Any]:
        """Checkout Session parameters (pure; unit-tested without the SDK)."""
        return {
            "mode": "payment",
            "client_reference_id": order.order_no,
            "success_url": return_url,
            "cancel_url": cancel_url,
            "line_items": [
                {
                    "quantity": 1,
                    "price_data": {
                        "currency": order.currency.lower(),
                        "unit_amount": to_minor(order.amount, order.currency),
                        "product_data": {"name": f"SuperDL balance top-up {order.order_no}"},
                    },
                }
            ],
            "expires_at": _clamp_expiry(order.expires_at.timestamp(), now_utc().timestamp()),
            "metadata": {"order_no": order.order_no},
            "payment_intent_data": {"metadata": {"order_no": order.order_no}},
        }

    async def create_payment(
        self, order: "Order", *, return_url: str, cancel_url: str
    ) -> PaymentInit:
        params = self.session_params(order, return_url=return_url, cancel_url=cancel_url)
        try:
            session = await run_in_sdk_pool(
                self._client.checkout.sessions.create,
                params=params,
                options={"idempotency_key": f"superdl-recharge:{order.order_no}"},
            )
        except Exception as exc:
            raise AppError(
                ErrorCode.PAYMENT_CHANNEL_ERROR,
                key="billing.stripeCreateFailed",
                params={"message": str(exc)},
            ) from exc
        return PaymentInit(url=session.url, channel_ref=session.id)

    async def parse_callback(self, headers: dict[str, str], body: bytes) -> CallbackResult | None:
        """Verify `Stripe-Signature` (timestamp tolerance 5 min) and map the event; events the
        platform does not act on are acknowledged with None."""
        import stripe

        try:
            event = stripe.Webhook.construct_event(
                body,
                header_value(headers, "Stripe-Signature"),
                self._webhook_secret,
                tolerance=SIGNATURE_TOLERANCE_SECONDS,
            )
        except (ValueError, stripe.SignatureVerificationError) as exc:
            raise channel_error("billing.stripeCallbackVerifyFailed") from exc
        obj = _as_dict(event.data.object)
        if event.type in _SESSION_PAID_EVENTS:
            return _session_result(obj) if obj.get("payment_status") == "paid" else None
        if event.type in _SESSION_FAILED_EVENTS:
            return _session_result(obj, success=False)
        if event.type == "charge.refunded":
            return _reversal(
                order_no=(obj.get("metadata") or {}).get("order_no"),
                payment_intent=obj.get("payment_intent"),
                amount=obj.get("amount"),
                reversed_amount=obj.get("amount_refunded"),
                currency=obj.get("currency"),
            )
        if event.type == "charge.dispute.created":
            return _reversal(
                order_no=None,
                payment_intent=obj.get("payment_intent"),
                amount=obj.get("amount"),
                reversed_amount=obj.get("amount"),
                currency=obj.get("currency"),
            )
        return None

    async def query_order(self, order: "Order") -> QueryResult:
        if not order.channel_ref:
            return QueryResult("unknown")
        try:
            session = _as_dict(
                await run_in_sdk_pool(self._client.checkout.sessions.retrieve, order.channel_ref)
            )
        except Exception as exc:
            raise AppError(
                ErrorCode.PAYMENT_CHANNEL_ERROR,
                key="billing.stripeQueryFailed",
                params={"message": str(exc)},
            ) from exc
        if session.get("status") == "complete" and session.get("payment_status") == "paid":
            currency = str(session.get("currency") or "").upper()
            return QueryResult(
                "paid",
                channel_txn_id=_txn_id(session),
                amount=from_minor(int(session.get("amount_total") or 0), currency),
                currency=currency,
            )
        if session.get("status") == "expired":
            return QueryResult("closed")
        return QueryResult("pending")


def _as_dict(obj: Any) -> dict[str, Any]:
    """Stripe resources are not dicts (v8+ SDK); normalise them (and plain dicts) to dicts."""
    if isinstance(obj, dict):
        return obj
    return obj.to_dict() if hasattr(obj, "to_dict") else dict(obj)


def _txn_id(session: Any) -> str:
    """PaymentIntent id (charge-level reference), or the session id before one exists."""
    intent = session.get("payment_intent")
    if isinstance(intent, dict):
        intent = intent.get("id")
    return str(intent or f"stripe-session:{session.get('id')}")


def _session_result(session: Any, *, success: bool = True) -> CallbackResult:
    currency = str(session.get("currency") or "").upper()
    return CallbackResult(
        order_no=session.get("client_reference_id")
        or (session.get("metadata") or {}).get("order_no"),
        channel_txn_id=_txn_id(session),
        amount=from_minor(int(session.get("amount_total") or 0), currency),
        success=success,
        currency=currency,
    )


def _reversal(
    *,
    order_no: str | None,
    payment_intent: str | None,
    amount: int | None,
    reversed_amount: int | None,
    currency: str | None,
) -> CallbackResult | None:
    """Refund / dispute on a credited charge: a reversal keyed by the PaymentIntent."""
    if not payment_intent or not currency:
        return None
    code = currency.upper()
    return CallbackResult(
        order_no=order_no,
        channel_txn_id=payment_intent,
        amount=from_minor(int(amount or 0), code),
        success=False,
        refund_amount=from_minor(int(reversed_amount or 0), code) or None,
        currency=code,
    )
