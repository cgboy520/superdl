"""Development mock channel: no signature, class-level in-memory ledger."""

import json
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, ClassVar

from app.modules.billing.payment_channels.base import (
    CallbackResult,
    PaymentInit,
    QueryResult,
    channel_error,
)

if TYPE_CHECKING:
    from app.modules.billing.models import Order


class MockChannel:
    """Development / test channel without signatures; successful callbacks are recorded in a
    class-level in-memory ledger that order queries read."""

    name = "mock"

    _channel_side: ClassVar[dict[str, tuple[str, Decimal]]] = {}

    @classmethod
    def mark_paid(cls, order_no: str, txn_id: str, amount: Decimal | str) -> None:
        cls._channel_side[order_no] = (txn_id, Decimal(str(amount)))

    @classmethod
    def reset(cls) -> None:
        cls._channel_side.clear()

    async def create_payment(
        self,
        order: "Order",
        *,
        return_url: str,  # noqa: ARG002
        cancel_url: str,  # noqa: ARG002
    ) -> PaymentInit:
        return PaymentInit(f"superdl-mock-pay://{order.order_no}?amount={order.amount}")

    async def parse_callback(self, headers: dict[str, str], body: bytes) -> CallbackResult:  # noqa: ARG002
        try:
            data = json.loads(body)
            result = CallbackResult(
                order_no=data["order_no"],
                channel_txn_id=data.get("txn_id", f"mock-{data['order_no']}"),
                amount=Decimal(str(data["amount"])),
                success=bool(data.get("success", True)),
                currency=data.get("currency"),
            )
        except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
            raise channel_error("billing.mockCallbackParseFailed") from exc
        if result.success and result.order_no:
            self.mark_paid(result.order_no, result.channel_txn_id, result.amount)
        return result

    async def query_order(self, order: "Order") -> QueryResult:
        hit = self._channel_side.get(order.order_no)
        if hit is None:
            return QueryResult("pending")
        return QueryResult("paid", channel_txn_id=hit[0], amount=hit[1])
