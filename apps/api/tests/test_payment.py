"""支付:充值单/mock 渠道/回调幂等。验收:重放回调不重复入账。"""

from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.timeutil import now_utc
from app.modules.billing.models import BalanceLedger, Order
from app.modules.billing.payment_service import close_expired_orders
from tests.test_account_auth import register


async def user_headers(client: AsyncClient, phone: str = "13700000001") -> dict[str, str]:
    data = await register(client, phone)
    return {"Authorization": f"Bearer {data['access_token']}"}


async def create_order(client: AsyncClient, headers: dict, amount: str = "50.00") -> dict:
    resp = await client.post(
        "/api/v1/wallet/recharges", json={"amount": amount, "channel": "mock"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def pay_mock(client: AsyncClient, order_no: str, amount: str, txn_id: str | None = None):
    return await client.post(
        "/api/v1/webhooks/mock",
        json={"order_no": order_no, "amount": amount, "txn_id": txn_id or f"tx-{order_no}"},
    )


class TestRecharge:
    async def test_full_recharge_flow(self, client: AsyncClient, sm):
        headers = await user_headers(client)
        order = await create_order(client, headers, "50.00")
        assert order["status"] == "pending"
        assert order["qr_url"].startswith("superdl-mock-pay://")

        resp = await pay_mock(client, order["order_no"], "50.00")
        assert resp.status_code == 200

        detail = (
            await client.get(f"/api/v1/wallet/recharges/{order['order_no']}", headers=headers)
        ).json()
        assert detail["status"] == "paid"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "50.00"

    async def test_replay_callback_no_double_credit(self, client: AsyncClient, sm):
        """验收核心:重放回调不重复入账。"""
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

    async def test_amount_mismatch_rejected(self, client: AsyncClient, sm):
        headers = await user_headers(client)
        order = await create_order(client, headers, "30.00")
        resp = await pay_mock(client, order["order_no"], "10.00")
        assert resp.status_code == 400
        assert resp.json()["code"] == "PAYMENT_CHANNEL_ERROR"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "0.00"

    async def test_idempotency_key_same_order(self, client: AsyncClient, sm):
        headers = {**(await user_headers(client)), "Idempotency-Key": "recharge-1"}
        a = await create_order(client, headers, "20.00")
        b = await create_order(client, headers, "20.00")
        assert a["order_no"] == b["order_no"]
        async with sm() as session:
            orders = (await session.execute(select(Order))).scalars().all()
        assert len(orders) == 1

    async def test_amount_limits(self, client: AsyncClient, sm):
        headers = await user_headers(client)
        resp = await client.post(
            "/api/v1/wallet/recharges",
            json={"amount": "0.50", "channel": "mock"},
            headers=headers,
        )
        assert resp.json()["code"] == "VALIDATION_ERROR"
        resp = await client.post(
            "/api/v1/wallet/recharges",
            json={"amount": "99999", "channel": "mock"},
            headers=headers,
        )
        assert resp.json()["code"] == "VALIDATION_ERROR"

    async def test_unknown_order_callback(self, client: AsyncClient, sm):
        resp = await pay_mock(client, "R-not-exists", "10.00")
        assert resp.status_code == 404

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
        # 关闭后的回调不入账
        await pay_mock(client, order["order_no"], "20.00")
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "0.00"

    async def test_wechat_channel_requires_credentials(self, client: AsyncClient, sm):
        headers = await user_headers(client)
        resp = await client.post(
            "/api/v1/wallet/recharges",
            json={"amount": "20.00", "channel": "wechat"},
            headers=headers,
        )
        assert resp.json()["code"] == "PAYMENT_CHANNEL_ERROR"

    async def test_recharge_requires_auth(self, client: AsyncClient, sm):
        resp = await client.post(
            "/api/v1/wallet/recharges", json={"amount": "20.00", "channel": "mock"}
        )
        assert resp.status_code == 401


class TestMockChannelProdGuard:
    async def test_mock_channel_refused_in_prod(self, client, sm, monkeypatch):
        """安全:生产环境 mock 渠道无条件拒绝(即使 payment_mock 误开)。"""
        from app.core.config import get_settings
        from app.core.errors import AppError
        from app.modules.billing.payment_channels import get_channel

        settings = get_settings()
        monkeypatch.setattr(settings, "environment", "prod")
        monkeypatch.setattr(settings, "payment_mock", True)
        import pytest as _pytest

        with _pytest.raises(AppError) as exc:
            get_channel("mock")
        assert exc.value.code == "PAYMENT_CHANNEL_ERROR"
