# pyright: reportPrivateUsage=false
"""Stripe Checkout channel: session parameters (amount in minor units, expiry clamp, order
references), signed-webhook verification, event → CallbackResult mapping, session lookup, and the
API round trip (checkout hand-off → signed callback credits once → refund freezes). The SDK client
is faked; no Stripe API is called."""

import json
import time
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
import stripe
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.platform_config import runtime_config_from_strings
from app.core.timeutil import now_utc
from app.modules.billing.models import Order, Wallet
from app.modules.billing.payment_channels.stripe import (
    SESSION_EXPIRY_MAX_SECONDS,
    SESSION_EXPIRY_MIN_SECONDS,
    StripeChannel,
    from_minor,
    to_minor,
)
from tests.helpers import set_platform_setting, user_headers

SECRET = "whsec_testsecret0123456789"
CFG = runtime_config_from_strings(
    {
        "payment_stripe_enabled": "true",
        "stripe_secret_key": "sk_test_0123456789abcdef",
        "stripe_webhook_secret": SECRET,
    }
)


class FakeSessions:
    """Records create() params and serves retrieve() from a dict of sessions."""

    def __init__(self) -> None:
        self.created: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.sessions: dict[str, dict[str, Any]] = {}

    def create(self, params: dict[str, Any], options: dict[str, Any]) -> Any:
        self.created.append((params, options))
        sid = f"cs_test_{len(self.created)}"
        self.sessions[sid] = {"id": sid, "status": "open", "payment_status": "unpaid"}
        return SimpleNamespace(id=sid, url=f"https://checkout.stripe.test/{sid}")

    def retrieve(self, sid: str) -> dict[str, Any]:
        return self.sessions[sid]


class FakeClient:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.checkout = SimpleNamespace(sessions=FakeSessions())


def _order(amount: str = "50.00", currency: str = "USD", ttl_seconds: int = 7200) -> Order:
    return Order(
        order_no="R20260916120000abcd1234",
        user_id=1,
        amount=Decimal(amount),
        currency=currency,
        channel="stripe",
        expires_at=now_utc() + timedelta(seconds=ttl_seconds),
    )


def _signed(
    payload: dict[str, Any], *, secret: str = SECRET, ts: int | None = None
) -> tuple[dict[str, str], bytes]:
    body = json.dumps(payload).encode()
    ts = ts or int(time.time())
    sig = stripe.WebhookSignature._compute_signature(f"{ts}.{body.decode()}", secret)
    return {"Stripe-Signature": f"t={ts},v1={sig}"}, body


def _event(kind: str, obj: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "evt_1",
        "object": "event",
        "api_version": "2025-01-01",
        "created": int(time.time()),
        "type": kind,
        "data": {"object": obj},
    }


def _session_obj(**over: Any) -> dict[str, Any]:
    base = {
        "id": "cs_test_1",
        "object": "checkout.session",
        "client_reference_id": "R1",
        "payment_intent": "pi_1",
        "payment_status": "paid",
        "status": "complete",
        "amount_total": 5000,
        "currency": "usd",
        "metadata": {"order_no": "R1"},
    }
    base.update(over)
    return base


def test_minor_unit_conversion():
    assert to_minor(Decimal("50.00"), "USD") == 5000
    assert to_minor(Decimal("1000"), "JPY") == 1000
    assert from_minor(5000, "usd") == Decimal("50.00")
    assert from_minor(1000, "JPY") == Decimal("1000")


class TestSessionParams:
    def test_params_carry_order_reference_amount_and_urls(self):
        channel = StripeChannel(CFG, client=FakeClient())
        order = _order()
        params = channel.session_params(
            order, return_url="https://c/billing?recharge=R", cancel_url="https://c/x"
        )
        assert params["mode"] == "payment" and params["client_reference_id"] == order.order_no
        assert params["metadata"] == {"order_no": order.order_no}
        assert params["payment_intent_data"] == {"metadata": {"order_no": order.order_no}}
        item = params["line_items"][0]["price_data"]
        assert (item["currency"], item["unit_amount"]) == ("usd", 5000)
        assert (
            params["success_url"].endswith("recharge=R") and params["cancel_url"] == "https://c/x"
        )

    def test_expiry_clamped_to_stripe_window(self):
        channel = StripeChannel(CFG, client=FakeClient())
        now = int(now_utc().timestamp())
        short = channel.session_params(_order(ttl_seconds=600), return_url="u", cancel_url="c")
        assert short["expires_at"] >= now + SESSION_EXPIRY_MIN_SECONDS - 2
        long = channel.session_params(_order(ttl_seconds=48 * 3600), return_url="u", cancel_url="c")
        assert long["expires_at"] <= now + SESSION_EXPIRY_MAX_SECONDS + 2
        normal = channel.session_params(_order(ttl_seconds=7200), return_url="u", cancel_url="c")
        assert abs(normal["expires_at"] - (now + 7200)) <= 2

    async def test_create_payment_uses_idempotency_key_and_returns_session(self):
        client = FakeClient()
        channel = StripeChannel(CFG, client=client)
        init = await channel.create_payment(_order(), return_url="u", cancel_url="c")
        assert (
            init.url.startswith("https://checkout.stripe.test/") and init.channel_ref == "cs_test_1"
        )
        _params, options = client.checkout.sessions.created[0]
        assert options == {"idempotency_key": "superdl-recharge:R20260916120000abcd1234"}

    def test_incomplete_credentials_rejected(self):
        with pytest.raises(AppError) as exc:
            StripeChannel(
                runtime_config_from_strings({"stripe_secret_key": "sk_test_x"}), client=FakeClient()
            )
        assert exc.value.message_key == "billing.stripeCredentialsIncomplete"


class TestWebhookParsing:
    def _channel(self) -> StripeChannel:
        return StripeChannel(CFG, client=FakeClient())

    async def test_completed_paid_maps_to_credit(self):
        headers, body = _signed(_event("checkout.session.completed", _session_obj()))
        result = await self._channel().parse_callback(headers, body)
        assert result is not None
        assert (result.order_no, result.channel_txn_id, result.success) == ("R1", "pi_1", True)
        assert result.amount == Decimal("50.00") and result.currency == "USD"

    async def test_completed_unpaid_waits_then_async_success_credits(self):
        headers, body = _signed(
            _event("checkout.session.completed", _session_obj(payment_status="unpaid"))
        )
        assert await self._channel().parse_callback(headers, body) is None
        headers, body = _signed(_event("checkout.session.async_payment_succeeded", _session_obj()))
        result = await self._channel().parse_callback(headers, body)
        assert result is not None and result.success is True

    @pytest.mark.parametrize(
        "kind", ["checkout.session.async_payment_failed", "checkout.session.expired"]
    )
    async def test_failed_and_expired_sessions_are_failures(self, kind):
        headers, body = _signed(
            _event(kind, _session_obj(payment_status="unpaid", status="expired"))
        )
        result = await self._channel().parse_callback(headers, body)
        assert result is not None and result.success is False and result.order_no == "R1"

    async def test_zero_decimal_currency(self):
        headers, body = _signed(
            _event("checkout.session.completed", _session_obj(amount_total=1000, currency="jpy"))
        )
        result = await self._channel().parse_callback(headers, body)
        assert result is not None and result.amount == Decimal("1000") and result.currency == "JPY"

    async def test_refund_and_dispute_are_reversals_keyed_by_payment_intent(self):
        charge = {
            "id": "ch_1",
            "object": "charge",
            "payment_intent": "pi_1",
            "amount": 5000,
            "amount_refunded": 2000,
            "currency": "usd",
            "metadata": {"order_no": "R1"},
        }
        headers, body = _signed(_event("charge.refunded", charge))
        result = await self._channel().parse_callback(headers, body)
        assert result is not None
        assert (result.order_no, result.channel_txn_id, result.success) == ("R1", "pi_1", False)
        assert result.refund_amount == Decimal("20.00")
        dispute = {
            "id": "dp_1",
            "object": "dispute",
            "payment_intent": "pi_1",
            "amount": 5000,
            "currency": "usd",
        }
        headers, body = _signed(_event("charge.dispute.created", dispute))
        result = await self._channel().parse_callback(headers, body)
        assert result is not None
        assert (
            result.order_no is None and result.channel_txn_id == "pi_1" and result.success is False
        )
        assert result.refund_amount == Decimal("50.00")

    async def test_unknown_event_is_acknowledged_as_none(self):
        headers, body = _signed(
            _event("payment_intent.created", {"id": "pi_9", "object": "payment_intent"})
        )
        assert await self._channel().parse_callback(headers, body) is None

    async def test_tampered_stale_and_missing_signatures_rejected(self):
        headers, body = _signed(_event("checkout.session.completed", _session_obj()))
        for bad_headers, bad_body in (
            (headers, body + b" "),
            (
                _signed(_event("checkout.session.completed", _session_obj()), secret="whsec_other")[
                    0
                ],
                body,
            ),
            (
                _signed(
                    _event("checkout.session.completed", _session_obj()), ts=int(time.time()) - 3600
                )[0],
                body,
            ),
            ({}, body),
        ):
            with pytest.raises(AppError) as exc:
                await self._channel().parse_callback(bad_headers, bad_body)
            assert exc.value.message_key == "billing.stripeCallbackVerifyFailed"


class TestQueryOrder:
    async def test_session_status_mapping(self):
        client = FakeClient()
        channel = StripeChannel(CFG, client=client)
        order = _order()
        assert (await channel.query_order(order)).status == "unknown"
        order.channel_ref = "cs_1"
        client.checkout.sessions.sessions["cs_1"] = {
            "id": "cs_1",
            "status": "complete",
            "payment_status": "paid",
            "payment_intent": "pi_7",
            "amount_total": 5000,
            "currency": "usd",
        }
        paid = await channel.query_order(order)
        assert (paid.status, paid.channel_txn_id, paid.amount, paid.currency) == (
            "paid",
            "pi_7",
            Decimal("50.00"),
            "USD",
        )
        client.checkout.sessions.sessions["cs_1"] = {
            "id": "cs_1",
            "status": "expired",
            "payment_status": "unpaid",
        }
        assert (await channel.query_order(order)).status == "closed"
        client.checkout.sessions.sessions["cs_1"] = {
            "id": "cs_1",
            "status": "open",
            "payment_status": "unpaid",
        }
        assert (await channel.query_order(order)).status == "pending"


class TestApiRoundTrip:
    async def _enable(self, sm, monkeypatch) -> None:
        import app.modules.billing.payment_channels as pc

        monkeypatch.setattr(stripe, "StripeClient", FakeClient)
        monkeypatch.setattr(pc, "_real_channel_cache", {})
        for key, value in (
            ("payment_stripe_enabled", "true"),
            ("stripe_secret_key", "sk_test_0123456789abcdef"),
            ("stripe_webhook_secret", SECRET),
        ):
            await set_platform_setting(sm, key, value)

    async def test_checkout_handoff_signed_callback_credits_once_and_refund_freezes(
        self, client: AsyncClient, sm, monkeypatch
    ):
        await self._enable(sm, monkeypatch)
        site = (await client.get("/api/v1/site-config")).json()
        assert {"name": "stripe", "presentation": "redirect"} in site["payment_channels"]
        headers = await user_headers(client, "u13700000501@test.local")
        resp = await client.post(
            "/api/v1/wallet/recharges",
            json={"amount": "50.00", "channel": "stripe"},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        order = resp.json()
        assert order["presentation"] == "redirect"
        assert order["payment_url"].startswith("https://checkout.stripe.test/")
        async with sm() as session:
            row = (
                await session.execute(select(Order).where(Order.order_no == order["order_no"]))
            ).scalar_one()
            assert row.channel_ref == "cs_test_1"
        currency = get_settings().platform_currency
        paid = _session_obj(
            client_reference_id=order["order_no"],
            payment_intent="pi_round",
            amount_total=to_minor(Decimal("50.00"), currency),
            currency=currency.lower(),
            metadata={"order_no": order["order_no"]},
        )
        for _ in range(2):
            sig_headers, body = _signed(_event("checkout.session.completed", paid))
            hook = await client.post("/api/v1/webhooks/stripe", content=body, headers=sig_headers)
            assert hook.status_code == 200, hook.text
            assert hook.json() == {"received": "ok"}
        wallet = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert wallet["balance"] == "50.00"
        charge = {
            "id": "ch_r",
            "object": "charge",
            "payment_intent": "pi_round",
            "amount": to_minor(Decimal("50.00"), currency),
            "amount_refunded": to_minor(Decimal("50.00"), currency),
            "currency": currency.lower(),
            "metadata": {},
        }
        sig_headers, body = _signed(_event("charge.refunded", charge))
        hook = await client.post("/api/v1/webhooks/stripe", content=body, headers=sig_headers)
        assert hook.status_code == 200, hook.text
        wallet = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert wallet["balance"] == "50.00"
        async with sm() as session:
            row = (
                await session.execute(select(Order).where(Order.order_no == order["order_no"]))
            ).scalar_one()
            assert row.channel_reversed_at is not None
            frozen = (
                await session.execute(select(Wallet.frozen).where(Wallet.user_id == row.user_id))
            ).scalar_one()
            assert frozen == Decimal("50.00")

    async def test_bad_signature_is_channel_error(self, client: AsyncClient, sm, monkeypatch):
        await self._enable(sm, monkeypatch)
        resp = await client.post(
            "/api/v1/webhooks/stripe", content=b"{}", headers={"Stripe-Signature": "t=1,v1=bad"}
        )
        assert resp.status_code >= 400
        assert resp.json()["message_key"] == "billing.stripeCallbackVerifyFailed"
