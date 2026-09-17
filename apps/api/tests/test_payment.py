"""Top-up orders, payment channels and callback idempotency."""

from datetime import timedelta
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.timeutil import now_utc
from app.modules.billing.models import BalanceLedger, Order, Wallet
from app.modules.billing.payment_service import close_expired_orders
from tests.helpers import admin_headers, create_order, pay_mock, user_headers


class TestRecharge:
    async def test_full_recharge_flow(self, client: AsyncClient, sm):
        headers = await user_headers(client)
        order = await create_order(client, headers, "50.00")
        assert order["status"] == "pending"
        assert order["payment_url"].startswith("superdl-mock-pay://")
        assert (
            order["presentation"] == "qr" and order["currency"] == get_settings().platform_currency
        )

        resp = await pay_mock(client, order["order_no"], "50.00")
        assert resp.status_code == 200

        detail = (
            await client.get(f"/api/v1/wallet/recharges/{order['order_no']}", headers=headers)
        ).json()
        assert detail["status"] == "paid"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "50.00"

    async def test_replay_callback_no_double_credit(self, client: AsyncClient, sm):
        """A replayed callback does not credit twice."""
        headers = await user_headers(client)
        order = await create_order(client, headers, "30.00")
        for _ in range(3):
            resp = await pay_mock(client, order["order_no"], "30.00", txn_id="fixed-txn")
            assert resp.status_code == 200
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "30.00"
        async with sm() as session:
            entries = (await session.execute(select(BalanceLedger))).scalars().all()
        assert len([e for e in entries if e.type == "recharge"]) == 1

    async def test_currency_mismatch_rejected(self, client: AsyncClient, sm):
        """A channel reporting another currency never credits, even with the right amount; the
        order stays pending and the matching callback still succeeds afterwards."""
        headers = await user_headers(client, "13800000104")
        order = await create_order(client, headers, "50.00")
        assert order["currency"] == get_settings().platform_currency
        resp = await client.post(
            "/api/v1/webhooks/mock",
            json={"order_no": order["order_no"], "amount": "50.00", "currency": "XXX"},
        )
        assert resp.status_code >= 400
        assert resp.json()["code"] == "PAYMENT_CHANNEL_ERROR"
        assert resp.json()["message_key"] == "billing.currencyMismatch"
        resp = await client.post(
            "/api/v1/webhooks/mock",
            json={
                "order_no": order["order_no"],
                "amount": "50.00",
                "currency": get_settings().platform_currency,
            },
        )
        assert resp.status_code == 200, resp.text
        wallet = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert wallet["balance"] == "50.00"

    async def test_amount_mismatch_rejected(self, client: AsyncClient, sm):
        headers = await user_headers(client)
        order = await create_order(client, headers, "30.00")
        resp = await pay_mock(client, order["order_no"], "10.00")
        assert resp.status_code == 400
        assert resp.json()["code"] == "PAYMENT_CHANNEL_ERROR"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "0.00"

    async def test_recharge_rate_limited_per_user(self, client: AsyncClient, sm):
        """Money endpoint rate limit: at most 10 top-up orders per user per hour, the 11th is 429
        with
        Retry-After."""
        headers = await user_headers(client)
        for _ in range(10):
            await create_order(client, headers, "1.00")
        resp = await client.post(
            "/api/v1/wallet/recharges",
            json={"amount": "1.00", "channel": "mock"},
            headers=headers,
        )
        assert resp.status_code == 429
        assert resp.json()["code"] == "RATE_LIMITED"
        assert resp.headers["retry-after"].isdigit()

    async def test_idempotency_key_same_order(self, client: AsyncClient, sm):
        headers = {**(await user_headers(client)), "Idempotency-Key": "recharge-1"}
        a = await create_order(client, headers, "20.00")
        resp = await client.post(
            "/api/v1/wallet/recharges",
            json={"amount": "20.00", "channel": "mock"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.headers["x-idempotent-replay"] == "true"
        b = resp.json()
        assert a["order_no"] == b["order_no"]
        async with sm() as session:
            orders = (await session.execute(select(Order))).scalars().all()
        assert len(orders) == 1

    async def test_idempotency_key_param_mismatch_409(self, client: AsyncClient, sm):
        """Same key, different params (amount changed): 409."""
        headers = {**(await user_headers(client, "13700000045")), "Idempotency-Key": "recharge-mix"}
        a = await create_order(client, headers, "20.00")
        resp = await client.post(
            "/api/v1/wallet/recharges",
            json={"amount": "21.00", "channel": "mock"},
            headers=headers,
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "common.idempotencyKeyMismatch"
        async with sm() as session:
            orders = (await session.execute(select(Order))).scalars().all()
        assert [o.order_no for o in orders] == [a["order_no"]]

    async def test_concurrent_same_key_first_request(self, client: AsyncClient, sm):
        """Concurrent first requests with the same key: one order, one 201 and one 200 replay, no
        500."""
        import asyncio

        headers = {**(await user_headers(client, "13700000039")), "Idempotency-Key": "race-1"}

        async def create():
            return await client.post(
                "/api/v1/wallet/recharges",
                json={"amount": "20.00", "channel": "mock"},
                headers=headers,
            )

        a, b = await asyncio.gather(create(), create())
        assert {a.status_code, b.status_code} == {200, 201}, (a.text, b.text)
        replayed = a if a.status_code == 200 else b
        assert replayed.headers["x-idempotent-replay"] == "true"
        assert a.json()["order_no"] == b.json()["order_no"]
        async with sm() as session:
            orders = (await session.execute(select(Order))).scalars().all()
        assert len(orders) == 1

    async def test_failure_callback_marks_order_failed(self, client: AsyncClient, sm):
        """A callback stating payment failure → the order becomes failed without credit."""
        headers = await user_headers(client, "13700000038")
        order = await create_order(client, headers, "20.00")
        resp = await client.post(
            "/api/v1/webhooks/mock",
            json={"order_no": order["order_no"], "amount": "20.00", "success": False},
        )
        assert resp.status_code == 200
        detail = (
            await client.get(f"/api/v1/wallet/recharges/{order['order_no']}", headers=headers)
        ).json()
        assert detail["status"] == "failed"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "0.00"

    async def test_reversal_on_paid_order_flagged_not_debited(self, client: AsyncClient, sm):
        """A credited order receives a channel close / refund notice: no automatic reversal,
        channel_reversed_at set, listed as an anomaly."""
        headers = await user_headers(client, "13700000044")
        order = await create_order(client, headers, "20.00")
        resp = await pay_mock(client, order["order_no"], "20.00")
        assert resp.status_code == 200
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "20.00"
        resp = await client.post(
            "/api/v1/webhooks/mock",
            json={"order_no": order["order_no"], "amount": "20.00", "success": False},
        )
        assert resp.status_code == 200
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "20.00"
        async with sm() as session:
            row = (
                await session.execute(select(Order).where(Order.order_no == order["order_no"]))
            ).scalar_one()
            assert row.channel_reversed_at is not None
        ah = await admin_headers(sm, client, role="finance")
        anomalies = (await client.get("/api/admin/v1/finance/anomalies", headers=ah)).json()
        assert any(
            a["kind"] == "channel_reversed" and a["order_no"] == order["order_no"]
            for a in anomalies
        )

    async def test_reversal_release_then_replay_does_not_refreeze(self, client: AsyncClient, sm):
        headers = await user_headers(client, "13700000046")
        order = await create_order(client, headers, "20.00")
        assert (await pay_mock(client, order["order_no"], "20.00")).status_code == 200
        reversal = {"order_no": order["order_no"], "amount": "20.00", "success": False}
        assert (await client.post("/api/v1/webhooks/mock", json=reversal)).status_code == 200

        async def frozen_of(user_id: int) -> Decimal:
            async with sm() as session:
                return (
                    await session.execute(select(Wallet.frozen).where(Wallet.user_id == user_id))
                ).scalar_one()

        async with sm() as session:
            uid = (
                await session.execute(
                    select(Order.user_id).where(Order.order_no == order["order_no"])
                )
            ).scalar_one()
        assert await frozen_of(uid) == Decimal("20.00")
        ah = await admin_headers(sm, client, role="finance")
        resp = await client.post(
            f"/api/admin/v1/finance/reversals/{order['order_no']}/resolve",
            json={"action": "release", "reason": "channel false alarm"},
            headers=ah,
        )
        assert resp.status_code == 200, resp.text
        assert (await client.post("/api/v1/webhooks/mock", json=reversal)).status_code == 200
        assert await frozen_of(uid) == Decimal("0.00")
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "20.00"
        async with sm() as session:
            row = (
                await session.execute(select(Order).where(Order.order_no == order["order_no"]))
            ).scalar_one()
            assert row.channel_reversed_at is not None
            assert row.channel_reversal_action == "release"
        anomalies = (await client.get("/api/admin/v1/finance/anomalies", headers=ah)).json()
        assert not any(
            a["kind"] == "channel_reversed" and a["order_no"] == order["order_no"]
            for a in anomalies
        )
        resp = await client.post(
            f"/api/admin/v1/finance/reversals/{order['order_no']}/resolve",
            json={"action": "chargeback", "reason": "repeated write-off: must be refused"},
            headers=ah,
        )
        assert resp.status_code == 409

    async def test_amount_bounds_rejected_at_contract_layer(self, client: AsyncClient):
        """Absurd amounts (1e30, negative) fail the contract (422); a value below the policy
        minimum passes the contract and is refused by the recharge_min policy (400)."""
        headers = await user_headers(client, "13700000045")
        for amount in ("1e30", "-5"):
            resp = await client.post(
                "/api/v1/wallet/recharges",
                json={"amount": amount, "channel": "mock"},
                headers=headers,
            )
            assert resp.status_code == 422, (amount, resp.text)
            assert resp.json()["code"] == "VALIDATION_ERROR"
        resp = await client.post(
            "/api/v1/wallet/recharges", json={"amount": "0.50", "channel": "mock"}, headers=headers
        )
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "billing.rechargeAmountOutOfRange"


class TestCallbackOnNonPendingOrders:
    """Callbacks after close that disagree with the channel."""

    async def test_expired_orders_closed(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        headers = await user_headers(client)
        order = await create_order(client, headers, "20.00")
        async with sm() as session:
            await session.execute(update(Order).values(expires_at=now_utc() - timedelta(minutes=1)))
            await session.commit()
        assert await close_expired_orders(sm) == 1
        detail = (
            await client.get(f"/api/v1/wallet/recharges/{order['order_no']}", headers=headers)
        ).json()
        assert detail["status"] == "closed"
        resp = await pay_mock(client, order["order_no"], "20.00")
        assert resp.status_code == 200
        detail = (
            await client.get(f"/api/v1/wallet/recharges/{order['order_no']}", headers=headers)
        ).json()
        assert detail["status"] == "paid"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "20.00"

    async def test_closed_order_callback_amount_mismatch_no_credit(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """Rescue after close only with a matching amount: a mismatch is still refused and goes to a
        manual adjustment."""
        headers = await user_headers(client, "13700000031")
        order = await create_order(client, headers, "20.00")
        async with sm() as session:
            await session.execute(
                update(Order).where(Order.order_no == order["order_no"]).values(status="closed")
            )
            await session.commit()
        resp = await pay_mock(client, order["order_no"], "19.99")
        assert resp.status_code == 400
        assert resp.json()["code"] == "PAYMENT_CHANNEL_ERROR"
        detail = (
            await client.get(f"/api/v1/wallet/recharges/{order['order_no']}", headers=headers)
        ).json()
        assert detail["status"] == "closed"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "0.00"

    async def test_callback_channel_mismatch_rejected(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """Callback channel ≠ order channel → refused (a cross-channel callback built straight at
        the
        service layer)."""
        import pytest as _pytest

        from app.core.errors import AppError
        from app.modules.billing.payment_channels import CallbackResult
        from app.modules.billing.payment_service import handle_callback

        headers = await user_headers(client, "13700000034")
        order = await create_order(client, headers, "20.00")
        async with sm() as session:
            with _pytest.raises(AppError) as exc:
                await handle_callback(
                    session,
                    "alipay",
                    CallbackResult(order["order_no"], "txn-x", Decimal("20.00"), True),
                )
        assert exc.value.code == "PAYMENT_CHANNEL_ERROR"


class TestRealChannelWebhookRoutes:
    async def test_alipay_webhook_plain_text_success(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession], monkeypatch
    ):
        """The Alipay ack is the plain text success."""
        from app.modules.billing.payment_channels import MockChannel

        async def fake_get_channel(name, session):
            return MockChannel()

        monkeypatch.setattr("app.modules.billing.webhooks_router.get_channel", fake_get_channel)
        headers = await user_headers(client, "13700000035")
        order = await create_order(client, headers, "20.00")
        async with sm() as session:
            await session.execute(
                update(Order).where(Order.order_no == order["order_no"]).values(channel="alipay")
            )
            await session.commit()
        resp = await client.post(
            "/api/v1/webhooks/alipay",
            json={"order_no": order["order_no"], "amount": "20.00", "txn_id": "ali-txn-1"},
        )
        assert resp.status_code == 200
        assert resp.text == "success"
        assert resp.headers["content-type"].startswith("text/plain")
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "20.00"

    async def test_wechat_webhook_success_envelope(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession], monkeypatch
    ):
        """The WeChat APIv3 ack is {"code": "SUCCESS"}; crediting goes through the same
        handle_callback."""
        from app.modules.billing.payment_channels import MockChannel

        async def fake_get_channel(name, session):
            return MockChannel()

        monkeypatch.setattr("app.modules.billing.webhooks_router.get_channel", fake_get_channel)
        headers = await user_headers(client, "13700000036")
        order = await create_order(client, headers, "30.00")
        async with sm() as session:
            await session.execute(
                update(Order).where(Order.order_no == order["order_no"]).values(channel="wechat")
            )
            await session.commit()
        resp = await client.post(
            "/api/v1/webhooks/wechatpay",
            json={"order_no": order["order_no"], "amount": "30.00", "txn_id": "wx-txn-1"},
        )
        assert resp.status_code == 200
        assert resp.json()["code"] == "SUCCESS"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "30.00"


class TestChannelFactory:
    async def test_real_channel_fingerprint_cache(self, sm, monkeypatch):
        """Channel instance fingerprint cache: unchanged config hits the cache, rotated credentials
        rebuild at once."""
        from dataclasses import replace

        import app.modules.billing.payment_channels as pc
        from app.core.platform_config import runtime_config_from_strings

        cfg = runtime_config_from_strings(
            {
                "alipay_app_id": "app-1",
                "alipay_private_key": "key-1",
                "alipay_public_key": "pub-1",
                "alipay_seller_id": "",
            }
        )

        async def fake_cfg(session):
            return cfg

        monkeypatch.setattr(pc, "get_runtime_config", fake_cfg)
        monkeypatch.setattr(pc, "_real_channel_cache", {})
        async with sm() as session:
            c1 = await pc.get_channel("alipay", session)
            c2 = await pc.get_channel("alipay", session)
            assert c1 is c2
            cfg = replace(cfg, alipay_private_key="key-2")
            c3 = await pc.get_channel("alipay", session)
            assert c3 is not c1


class TestMockChannelGuard:
    async def test_mock_channel_refused_when_disabled(self, sm, monkeypatch):
        """With payment_mock=false the mock channel is unavailable."""
        from app.core.config import get_settings
        from app.core.errors import AppError
        from app.modules.billing.payment_channels import get_channel

        monkeypatch.setattr(get_settings(), "payment_mock", False)
        import pytest as _pytest

        async with sm() as session:
            with _pytest.raises(AppError) as exc:
                await get_channel("mock", session)
        assert exc.value.message_key == "billing.mockDevOnly"
