"""支付:充值单/mock 渠道/回调幂等。验收:重放回调不重复入账。"""

from datetime import timedelta
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.timeutil import now_utc
from app.modules.billing.models import BalanceLedger, Order
from app.modules.billing.payment_service import close_expired_orders
from tests.helpers import admin_headers, create_order, pay_mock, user_headers


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

    async def test_recharge_rate_limited_per_user(self, client: AsyncClient, sm):
        """资金端点限流:同一用户 1 小时最多 10 张充值单,第 11 张 429 带 Retry-After。"""
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
        assert resp.status_code == 200  # 重放:200 + X-Idempotent-Replay,而非 201
        assert resp.headers["x-idempotent-replay"] == "true"
        b = resp.json()
        assert a["order_no"] == b["order_no"]
        async with sm() as session:
            orders = (await session.execute(select(Order))).scalars().all()
        assert len(orders) == 1

    async def test_idempotency_key_param_mismatch_409(self, client: AsyncClient, sm):
        """同键异参(改了金额):显式 409,绝不静默返回上一单(弱键复用防线)。"""
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
        """同键并发首请求(双击/超时重试):负方回查返回同一订单,不许 500。
        冒烟性质:两路是否真撞到「先 SELECT 后 INSERT」的唯一约束兜底分支取决于调度,
        断言只锁最终结果(一单、一 201 一 200 重放)。"""
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

    async def test_reversal_on_paid_order_flagged_not_debited(self, client: AsyncClient, sm):
        """已入账订单收到渠道关单/退款通知:不自动冲账,落 channel_reversed_at 标记,
        进异常清单 channel_reversed 分桶供人工核销。"""
        headers = await user_headers(client, "13700000044")
        order = await create_order(client, headers, "20.00")
        resp = await pay_mock(client, order["order_no"], "20.00")
        assert resp.status_code == 200
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "20.00"
        # 渠道侧反转(商户后台退款/关单)通知到达
        resp = await client.post(
            "/api/v1/webhooks/mock",
            json={"order_no": order["order_no"], "amount": "20.00", "success": False},
        )
        assert resp.status_code == 200
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "20.00"  # 不自动冲账
        async with sm() as session:
            row = (
                await session.execute(select(Order).where(Order.order_no == order["order_no"]))
            ).scalar_one()
            assert row.channel_reversed_at is not None
        # 异常清单出现 channel_reversed 分桶
        ah = await admin_headers(sm, client, role="finance")
        anomalies = (await client.get("/api/admin/v1/finance/anomalies", headers=ah)).json()
        assert any(
            a["kind"] == "channel_reversed" and a["order_no"] == order["order_no"]
            for a in anomalies
        )

    async def test_amount_bounds_rejected_at_contract_layer(self, client: AsyncClient):
        """充值金额上下限只在契约层校验:低于下限、超大(1e30)、负数一律 422
        (1e30 若先量化会在 as_amount 抛 InvalidOperation 漏成 500)。"""
        headers = await user_headers(client, "13700000045")
        for amount in ("0.50", "1e30", "-5"):
            resp = await client.post(
                "/api/v1/wallet/recharges",
                json={"amount": amount, "channel": "mock"},
                headers=headers,
            )
            assert resp.status_code == 422, (amount, resp.text)
            assert resp.json()["code"] == "VALIDATION_ERROR"


class TestMockCallbackParsing:
    """mock 回调的畸形报文(非 JSON 对象 / 缺字段 / 金额非数值走同一 except 分支):
    一律 400 PAYMENT_CHANNEL_ERROR,不许漏成 500。"""

    async def test_malformed_body_400(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/webhooks/mock",
            content=b"[1,2,3]",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "PAYMENT_CHANNEL_ERROR"


class TestCallbackOnNonPendingOrders:
    """关单/失败单后的回调、渠道不符与渠道凭据缺失。"""

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

    async def test_failed_order_callback_rescued(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """failed 订单(渠道中间态误迁移)收到验签通过的成功回调:与 closed 同路径自动入账。"""
        headers = await user_headers(client, "13700000033")
        order = await create_order(client, headers, "20.00")
        async with sm() as session:
            await session.execute(
                update(Order).where(Order.order_no == order["order_no"]).values(status="failed")
            )
            await session.commit()
        resp = await pay_mock(client, order["order_no"], "20.00")
        assert resp.status_code == 200
        detail = (
            await client.get(f"/api/v1/wallet/recharges/{order['order_no']}", headers=headers)
        ).json()
        assert detail["status"] == "paid"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "20.00"

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
    async def test_alipay_webhook_plain_text_success(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession], monkeypatch
    ):
        """支付宝应答必须是纯文本 success(JSON 会被渠道判失败并重试 8 次)。"""
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


class TestMockChannelGuard:
    async def test_mock_channel_refused_when_disabled(self, sm, monkeypatch):
        """payment_mock=false 时无验签的 mock 渠道不可用(prod 下 payment_mock 必为 false 由
        Settings 校验保证,渠道层只看这一个开关)。"""
        from app.core.config import get_settings
        from app.core.errors import AppError
        from app.modules.billing.payment_channels import get_channel

        monkeypatch.setattr(get_settings(), "payment_mock", False)
        import pytest as _pytest

        async with sm() as session:
            with _pytest.raises(AppError) as exc:
                await get_channel("mock", session)
        assert exc.value.message_key == "billing.mockDevOnly"
