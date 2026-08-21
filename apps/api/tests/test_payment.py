"""支付:充值单/mock 渠道/回调幂等。验收:重放回调不重复入账。"""

from datetime import timedelta
from decimal import Decimal

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

    async def test_failure_callback_marks_order_failed(self, client: AsyncClient, sm):
        """渠道回调明示支付失败 → 订单转 failed,不入账。"""
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
        # 关单后的有效成功回调(验签 + 金额一致):自动入账
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
        """关单救回仅限金额一致:金额不符仍拒绝,走人工调账。"""
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

    async def test_closed_order_failure_callback_stays_closed(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """关单后到达的失败回调:不救回、不改状态。"""
        headers = await user_headers(client, "13700000032")
        order = await create_order(client, headers, "20.00")
        async with sm() as session:
            await session.execute(
                update(Order).where(Order.order_no == order["order_no"]).values(status="closed")
            )
            await session.commit()
        resp = await client.post(
            "/api/v1/webhooks/mock",
            json={"order_no": order["order_no"], "amount": "20.00", "success": False},
        )
        assert resp.status_code == 200
        detail = (
            await client.get(f"/api/v1/wallet/recharges/{order['order_no']}", headers=headers)
        ).json()
        assert detail["status"] == "closed"

    async def test_failed_order_callback_not_rescued(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """自动救回仅限 closed(超时关单):failed 订单收到成功回调不入账。"""
        headers = await user_headers(client, "13700000033")
        order = await create_order(client, headers, "20.00")
        async with sm() as session:
            await session.execute(
                update(Order).where(Order.order_no == order["order_no"]).values(status="failed")
            )
            await session.commit()
        resp = await pay_mock(client, order["order_no"], "20.00")
        assert resp.status_code == 200
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "0.00"

    async def test_callback_channel_mismatch_rejected(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """回调渠道与订单渠道不符 → 拒绝(服务层直连构造跨渠道回调)。"""
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

    async def test_wechat_channel_requires_credentials(self, client: AsyncClient, sm):
        headers = await user_headers(client)
        resp = await client.post(
            "/api/v1/wallet/recharges",
            json={"amount": "20.00", "channel": "wechat"},
            headers=headers,
        )
        assert resp.json()["code"] == "PAYMENT_CHANNEL_ERROR"


class TestRealChannelWebhookRoutes:
    async def test_wechat_webhook_without_credentials(self, client: AsyncClient, sm):
        resp = await client.post("/api/v1/webhooks/wechatpay", content=b"{}")
        assert resp.status_code == 400
        assert resp.json()["code"] == "PAYMENT_CHANNEL_ERROR"

    async def test_alipay_webhook_without_credentials(self, client: AsyncClient, sm):
        resp = await client.post("/api/v1/webhooks/alipay", content=b"a=1")
        assert resp.status_code == 400
        assert resp.json()["code"] == "PAYMENT_CHANNEL_ERROR"

    async def test_alipay_webhook_plain_text_success(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession], monkeypatch
    ):
        """P1 回归:支付宝应答必须是纯文本 success(JSON 会被渠道判失败重试 8 次)。"""
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
        """微信 APIv3 应答 {"code": "SUCCESS"};入账走同一 handle_callback。"""
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
    async def test_unknown_channel_rejected(self, sm):
        import pytest as _pytest

        from app.core.errors import AppError
        from app.modules.billing.payment_channels import get_channel

        async with sm() as session:
            with _pytest.raises(AppError) as exc:
                await get_channel("paypal", session)
        assert exc.value.code == "VALIDATION_ERROR"

    async def test_real_channel_fingerprint_cache(self, sm, monkeypatch):
        """渠道实例指纹缓存:配置不变命中缓存,凭据轮换立即重建(免重启)。"""
        import app.modules.billing.payment_channels as pc

        cfg = dict.fromkeys(pc.WECHAT_CFG_KEYS, "") | {
            "alipay_app_id": "app-1",
            "alipay_private_key": "key-1",
            "alipay_public_key": "pub-1",
            "alipay_seller_id": "",
        }

        async def fake_cfg(session):
            return dict(cfg)

        monkeypatch.setattr(pc, "get_effective_platform_config", fake_cfg)
        monkeypatch.setattr(pc, "_real_channel_cache", {})
        async with sm() as session:
            c1 = await pc.get_channel("alipay", session)
            c2 = await pc.get_channel("alipay", session)
            assert c1 is c2
            cfg["alipay_private_key"] = "key-2"
            c3 = await pc.get_channel("alipay", session)
            assert c3 is not c1


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

        async with sm() as session:
            with _pytest.raises(AppError) as exc:
                await get_channel("mock", session)
        assert exc.value.code == "PAYMENT_CHANNEL_ERROR"
