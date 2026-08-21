"""支付对账闭环:查单 poller 收敛丢回调、人工补单(渠道核验制)、异常清单。

验收核心:丢回调场景下钱不丢 —— poller 或补单入账,且重复执行零重复入账。
"""

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.core.timeutil import now_utc
from app.modules.billing.models import Order, Wallet
from app.modules.billing.payment_channels import MockChannel
from app.modules.billing.payment_service import reconcile_pending_orders
from tests.test_catalog import admin_headers
from tests.test_payment import create_order, user_headers


@pytest.fixture(autouse=True)
def _reset_mock_channel():
    MockChannel.reset()
    yield
    MockChannel.reset()


async def _backdate_order(sm, order_no: str, minutes: int) -> None:
    async with sm() as session:
        await session.execute(
            update(Order)
            .where(Order.order_no == order_no)
            .values(created_at=now_utc() - timedelta(minutes=minutes))
        )
        await session.commit()


class TestReconcilePoller:
    async def test_lost_callback_recovered_and_idempotent(self, client: AsyncClient, sm):
        """渠道已付但回调丢失 → poller 查单入账;重复执行零重复入账。"""
        headers = await user_headers(client, "13700000021")
        order = await create_order(client, headers, "66.00")
        # 渠道侧已支付,但没有任何回调进来
        MockChannel.mark_paid(order["order_no"], "txn-lost-1", "66.00")
        await _backdate_order(sm, order["order_no"], 2)

        assert await reconcile_pending_orders(sm) == 1
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "66.00"

        # 幂等:再跑一轮不重复入账
        assert await reconcile_pending_orders(sm) == 0
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "66.00"

    async def test_unpaid_order_untouched(self, client: AsyncClient, sm):
        headers = await user_headers(client, "13700000022")
        order = await create_order(client, headers, "10.00")
        await _backdate_order(sm, order["order_no"], 2)
        assert await reconcile_pending_orders(sm) == 0
        detail = (
            await client.get(f"/api/v1/wallet/recharges/{order['order_no']}", headers=headers)
        ).json()
        assert detail["status"] == "pending"

    async def test_skipped_when_lock_held(self, client: AsyncClient, sm):
        """advisory lock 已被占(另一副本在跑)→ 本轮直接让出。"""
        from app.core.locks import LockKey, try_advisory_lock

        async with sm() as session, try_advisory_lock(session, LockKey.PAYMENT_RECONCILE) as got:
            assert got
            assert await reconcile_pending_orders(sm) == 0

    async def test_channel_unreachable_skipped(self, client: AsyncClient, sm):
        """渠道不可达(如凭据未配)→ 跳过该单,下轮再试,不阻塞其他单。"""
        headers = await user_headers(client, "13700000027")
        order = await create_order(client, headers, "12.00")
        async with sm() as session:
            await session.execute(
                update(Order).where(Order.order_no == order["order_no"]).values(channel="wechat")
            )
            await session.commit()
        await _backdate_order(sm, order["order_no"], 2)
        assert await reconcile_pending_orders(sm) == 0
        detail = (
            await client.get(f"/api/v1/wallet/recharges/{order['order_no']}", headers=headers)
        ).json()
        assert detail["status"] == "pending"


class TestBackfill:
    async def test_backfill_closed_order_after_verify(self, client: AsyncClient, sm):
        """超时关单但渠道已付:核验 → 补单入账;重复补单被拒。"""
        headers = await user_headers(client, "13700000023")
        order = await create_order(client, headers, "88.00")
        async with sm() as session:
            await session.execute(
                update(Order).where(Order.order_no == order["order_no"]).values(status="closed")
            )
            await session.commit()
        MockChannel.mark_paid(order["order_no"], "txn-backfill-1", "88.00")

        ah = await admin_headers(sm, client, role="finance")
        verify = await client.post(
            f"/api/admin/v1/finance/orders/{order['order_no']}/verify", headers=ah
        )
        assert verify.status_code == 200
        assert verify.json()["channel_status"] == "paid"
        assert verify.json()["matches"] is True

        resp = await client.post(
            f"/api/admin/v1/finance/orders/{order['order_no']}/backfill",
            json={"reason": "回调丢失,查单确认后补入账"},
            headers=ah,
        )
        assert resp.status_code == 200, resp.text
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "88.00"

        # 重复补单 → 409 冲突,余额不变
        resp = await client.post(
            f"/api/admin/v1/finance/orders/{order['order_no']}/backfill",
            json={"reason": "重复操作"},
            headers=ah,
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "CONFLICT"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "88.00"

    async def test_backfill_refused_when_channel_unpaid(self, client: AsyncClient, sm):
        """渠道侧未支付 → 补单被拒(操作者无法凭空造账)。"""
        headers = await user_headers(client, "13700000024")
        order = await create_order(client, headers, "20.00")
        ah = await admin_headers(sm, client, role="finance")
        resp = await client.post(
            f"/api/admin/v1/finance/orders/{order['order_no']}/backfill",
            json={"reason": "用户声称已付"},
            headers=ah,
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "PAYMENT_CHANNEL_ERROR"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "0.00"

    async def test_backfill_refused_on_failed_status(self, client: AsyncClient, sm):
        """failed 状态不可补单(仅 pending/closed 可救)。"""
        headers = await user_headers(client, "13700000029")
        order = await create_order(client, headers, "11.00")
        async with sm() as session:
            await session.execute(
                update(Order).where(Order.order_no == order["order_no"]).values(status="failed")
            )
            await session.commit()
        MockChannel.mark_paid(order["order_no"], "txn-failed-order", "11.00")
        ah = await admin_headers(sm, client, role="finance")
        resp = await client.post(
            f"/api/admin/v1/finance/orders/{order['order_no']}/backfill",
            json={"reason": "测试"},
            headers=ah,
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "CONFLICT"

    async def test_backfill_refused_on_amount_mismatch(self, client: AsyncClient, sm):
        headers = await user_headers(client, "13700000025")
        order = await create_order(client, headers, "30.00")
        MockChannel.mark_paid(order["order_no"], "txn-mismatch", "29.90")
        ah = await admin_headers(sm, client, role="finance")
        resp = await client.post(
            f"/api/admin/v1/finance/orders/{order['order_no']}/backfill",
            json={"reason": "核验"},
            headers=ah,
        )
        assert resp.status_code == 400
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "0.00"


class TestAnomalies:
    async def test_three_kinds_listed(self, client: AsyncClient, sm):
        headers = await user_headers(client, "13700000026")
        stale = await create_order(client, headers, "15.00")
        await _backdate_order(sm, stale["order_no"], 15)
        closed = await create_order(client, headers, "25.00")
        await client.get("/api/v1/wallet", headers=headers)  # 触发钱包创建
        async with sm() as session:
            await session.execute(
                update(Order).where(Order.order_no == closed["order_no"]).values(status="closed")
            )
            # 负余额钱包
            wallet_row = (await session.execute(select(Wallet).limit(1))).scalar_one_or_none()
            if wallet_row is not None:
                wallet_row.balance = -5
            await session.commit()

        ah = await admin_headers(sm, client, role="finance")
        resp = await client.get("/api/admin/v1/finance/anomalies", headers=ah)
        assert resp.status_code == 200
        kinds = {item["kind"] for item in resp.json()}
        assert "lost_callback" in kinds
        assert "closed_order" in kinds
        assert "negative_balance" in kinds
