"""Payment-channel protocol and the helpers every channel shares: result types, the SDK thread
pool, callback header and freshness checks."""

import asyncio
import functools
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, Protocol

from app.core.errors import AppError, ErrorCode
from app.core.timeutil import now_utc

if TYPE_CHECKING:
    from app.modules.billing.models import Order


@dataclass(frozen=True)
class PaymentInit:
    """What the channel hands back on order creation: the QR payload or checkout URL the console
    presents, and the channel-side reference to persist for later lookups (None when absent)."""

    url: str
    channel_ref: str | None = None


class CallbackResult:
    """Parsed payment callback; `success` = paid, `refund_amount` = channel-side refund,
    `currency` = ISO code reported by the channel (None when the wire format has none).
    `order_no` may be None for reversals that only carry the channel transaction id."""

    def __init__(
        self,
        order_no: str | None,
        channel_txn_id: str,
        amount: Decimal,
        success: bool,
        refund_amount: Decimal | None = None,
        *,
        currency: str | None = None,
    ) -> None:
        self.order_no = order_no
        self.channel_txn_id = channel_txn_id
        self.amount = amount
        self.success = success
        self.refund_amount = refund_amount
        self.currency = currency


SDK_TIMEOUT = (5, 10)
SDK_TIMEOUT_SECONDS = 10
CALLBACK_FRESHNESS_SECONDS = 15 * 60
_SDK_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="payment-sdk")


async def run_in_sdk_pool(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_SDK_EXECUTOR, functools.partial(fn, *args, **kwargs))


def header_value(headers: Mapping[str, str], name: str) -> str:
    """Read the first header of that name case-insensitively, empty string when missing."""
    lowered = name.lower()
    for k, v in headers.items():
        if k.lower() == lowered:
            return v
    return ""


def channel_error(key: str) -> AppError:
    """Unified shape of channel-side errors (incomplete credentials / signature failure / merchant
    mismatch / expired callback ...)."""
    return AppError(ErrorCode.PAYMENT_CHANNEL_ERROR, key=key)


def assert_callback_fresh(ts: datetime | None, *, key: str) -> None:
    """Reject callback timestamps that are missing or outside ±CALLBACK_FRESHNESS_SECONDS."""
    if ts is None or abs((now_utc() - ts).total_seconds()) > CALLBACK_FRESHNESS_SECONDS:
        raise channel_error(key)


class QueryResult:
    """Channel-side order status, transaction id, amount and currency (None = not reported)."""

    def __init__(
        self,
        status: Literal["paid", "pending", "closed", "unknown"],
        channel_txn_id: str | None = None,
        amount: Decimal | None = None,
        *,
        currency: str | None = None,
    ) -> None:
        self.status = status
        self.channel_txn_id = channel_txn_id
        self.amount = amount
        self.currency = currency


class PaymentChannel(Protocol):
    name: str

    async def create_payment(
        self, order: "Order", *, return_url: str, cancel_url: str
    ) -> PaymentInit:
        """Start a payment: QR payload or checkout URL plus the channel reference. `return_url`
        and `cancel_url` are where a redirect channel sends the payer back; QR channels ignore
        them."""
        ...

    async def parse_callback(self, headers: dict[str, str], body: bytes) -> CallbackResult | None:
        """Verify and parse a callback; a bad signature raises AppError(PAYMENT_CHANNEL_ERROR).
        None = verified but irrelevant event (acknowledge without touching any order)."""
        ...

    async def query_order(self, order: "Order") -> QueryResult:
        """Query the channel order status, transaction id and amount; failures raise."""
        ...
