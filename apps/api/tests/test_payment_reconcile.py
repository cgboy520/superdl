"""支付对账闭环:查单 poller 收敛丢回调、人工补单(渠道核验制)、异常清单;重复执行零重复入账。"""

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.core.timeutil import now_utc
from app.modules.billing.models import Order, Wallet
from app.modules.billing.payment_channels import MockChannel
from app.modules.billing.payment_service import reconcile_pending_orders
from tests.helpers import admin_headers, create_order, user_headers


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
        # 渠道侧已支付,无回调
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

    async def test_failed_order_recovered_by_poller(self, client: AsyncClient, sm):
        """failed 订单(渠道中间态误迁移)渠道侧实为已付 → poller 收敛入账;重复执行幂等。"""
        headers = await user_headers(client, "13700000042")
        order = await create_order(client, headers, "33.00")
        async with sm() as session:
            await session.execute(
                update(Order).where(Order.order_no == order["order_no"]).values(status="failed")
            )
            await session.commit()
        MockChannel.mark_paid(order["order_no"], "txn-failed-poll", "33.00")
        await _backdate_order(sm, order["order_no"], 2)

        assert await reconcile_pending_orders(sm) == 1
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "33.00"
        # 幂等:再跑一轮不重复入账
        assert await reconcile_pending_orders(sm) == 0
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "33.00"

    async def test_closed_order_recovered_by_poller(self, client: AsyncClient, sm):
        """关单前最后一刻支付成功但回调丢失:closed 单纳入收敛扫描,poller 自动救回。"""
        headers = await user_headers(client, "13700000044")
        order = await create_order(client, headers, "55.00")
        async with sm() as session:
            await session.execute(
                update(Order).where(Order.order_no == order["order_no"]).values(status="closed")
            )
            await session.commit()
        MockChannel.mark_paid(order["order_no"], "txn-closed-poll", "55.00")
        await _backdate_order(sm, order["order_no"], 2)

        assert await reconcile_pending_orders(sm) == 1
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "55.00"
        # 幂等:再跑一轮不重复入账
        assert await reconcile_pending_orders(sm) == 0
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "55.00"

    async def test_stale_closed_order_outside_window_not_scanned(self, client: AsyncClient, sm):
        """expires_at 超出 48h 的 closed 旧单不参与查单。"""
        headers = await user_headers(client, "13700000046")
        order = await create_order(client, headers, "45.00")
        async with sm() as session:
            await session.execute(
                update(Order)
                .where(Order.order_no == order["order_no"])
                .values(
                    status="closed",
                    expires_at=now_utc() - timedelta(hours=49),
                )
            )
            await session.commit()
        MockChannel.mark_paid(order["order_no"], "txn-stale-closed", "45.00")
        await _backdate_order(sm, order["order_no"], 2)  # created_at 在窗内:仅靠 expires_at 排除
        assert await reconcile_pending_orders(sm) == 0
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "0.00"

    async def test_stale_failed_order_outside_window_not_scanned(self, client: AsyncClient, sm):
        """48h 窗口外的 failed 旧单不参与查单。"""
        headers = await user_headers(client, "13700000043")
        order = await create_order(client, headers, "44.00")
        async with sm() as session:
            await session.execute(
                update(Order).where(Order.order_no == order["order_no"]).values(status="failed")
            )
            await session.commit()
        MockChannel.mark_paid(order["order_no"], "txn-stale-failed", "44.00")
        await _backdate_order(sm, order["order_no"], 49 * 60)
        assert await reconcile_pending_orders(sm) == 0
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "0.00"

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

    async def test_single_order_failure_does_not_abort_round(self, client: AsyncClient, sm):
        """单笔入账失败不中断整轮。"""
        headers_bad = await user_headers(client, "13700000031")
        bad = await create_order(client, headers_bad, "66.00")
        headers_good = await user_headers(client, "13700000032")
        good = await create_order(client, headers_good, "20.00")
        # 坏单:渠道侧金额不符 → handle_callback 抛 PAYMENT_CHANNEL_ERROR
        MockChannel.mark_paid(bad["order_no"], "txn-bad-amount", "65.90")
        MockChannel.mark_paid(good["order_no"], "txn-good", "20.00")
        await _backdate_order(sm, bad["order_no"], 2)
        await _backdate_order(sm, good["order_no"], 2)

        assert await reconcile_pending_orders(sm) == 1  # 只有好单入账
        bad_detail = (
            await client.get(f"/api/v1/wallet/recharges/{bad['order_no']}", headers=headers_bad)
        ).json()
        assert bad_detail["status"] == "pending"  # 留给人工核验,不静默入账
        w = (await client.get("/api/v1/wallet", headers=headers_good)).json()
        assert w["balance"] == "20.00"


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
        assert resp.status_code == 409
        assert resp.json()["code"] == "CONFLICT"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "88.00"

    async def test_backfill_idempotency_key_replay(self, client: AsyncClient, sm):
        """补单支持 Idempotency-Key:同键重放回当前状态而非 409;无键重试维持 409。"""
        headers = await user_headers(client, "13700000041")
        order = await create_order(client, headers, "66.00")
        MockChannel.mark_paid(order["order_no"], "txn-backfill-idem", "66.00")

        ah = await admin_headers(sm, client, role="finance")
        keyed = {**ah, "Idempotency-Key": "backfill-20260822-01"}
        r1 = await client.post(
            f"/api/admin/v1/finance/orders/{order['order_no']}/backfill",
            json={"reason": "回调丢失"},
            headers=keyed,
        )
        assert r1.status_code == 200, r1.text
        assert r1.json()["status"] == "paid"
        # 同键重放:返回当前状态 + X-Idempotent-Replay,不重复入账
        r2 = await client.post(
            f"/api/admin/v1/finance/orders/{order['order_no']}/backfill",
            json={"reason": "回调丢失"},
            headers=keyed,
        )
        assert r2.status_code == 200, r2.text
        assert r2.headers["x-idempotent-replay"] == "true"
        assert r2.json()["order_no"] == order["order_no"]
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "66.00"

    async def test_backfill_key_reused_on_other_order_conflicts(self, client: AsyncClient, sm):
        """同一幂等键用到另一笔订单:409 并指明持键订单。"""
        headers = await user_headers(client, "13700000042")
        order_a = await create_order(client, headers, "61.00")
        order_b = await create_order(client, headers, "62.00")
        MockChannel.mark_paid(order_a["order_no"], "txn-backfill-a", "61.00")
        MockChannel.mark_paid(order_b["order_no"], "txn-backfill-b", "62.00")

        ah = await admin_headers(sm, client, role="finance")
        keyed = {**ah, "Idempotency-Key": "backfill-shared-key"}
        r1 = await client.post(
            f"/api/admin/v1/finance/orders/{order_a['order_no']}/backfill",
            json={"reason": "首单补账"},
            headers=keyed,
        )
        assert r1.status_code == 200, r1.text
        r2 = await client.post(
            f"/api/admin/v1/finance/orders/{order_b['order_no']}/backfill",
            json={"reason": "同键换单"},
            headers=keyed,
        )
        assert r2.status_code == 409
        assert r2.json()["message_key"] == "billing.backfillKeyInUse"
        # order_b 未入账
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "61.00"

    async def test_backfill_refused_when_channel_unpaid(self, client: AsyncClient, sm):
        """渠道侧未支付 → 补单被拒。"""
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

    async def test_backfill_failed_order_after_verify(self, client: AsyncClient, sm):
        """failed 订单渠道核验为已支付后同样可补单。"""
        headers = await user_headers(client, "13700000029")
        order = await create_order(client, headers, "11.00")
        async with sm() as session:
            await session.execute(
                update(Order).where(Order.order_no == order["order_no"]).values(status="failed")
            )
            await session.commit()
        MockChannel.mark_paid(order["order_no"], "txn-failed-order", "11.00")
        ah = await admin_headers(sm, client, role="finance")
        verify = await client.post(
            f"/api/admin/v1/finance/orders/{order['order_no']}/verify", headers=ah
        )
        assert verify.status_code == 200
        assert verify.json()["matches"] is True
        resp = await client.post(
            f"/api/admin/v1/finance/orders/{order['order_no']}/backfill",
            json={"reason": "下单失败的订单,渠道侧实为已付"},
            headers=ah,
        )
        assert resp.status_code == 200, resp.text
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "11.00"

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
    async def test_four_kinds_listed(self, client: AsyncClient, sm):
        headers = await user_headers(client, "13700000026")
        stale = await create_order(client, headers, "15.00")
        await _backdate_order(sm, stale["order_no"], 15)
        closed = await create_order(client, headers, "25.00")
        failed = await create_order(client, headers, "35.00")
        await client.get("/api/v1/wallet", headers=headers)  # 触发钱包创建
        async with sm() as session:
            await session.execute(
                update(Order).where(Order.order_no == closed["order_no"]).values(status="closed")
            )
            await session.execute(
                update(Order).where(Order.order_no == failed["order_no"]).values(status="failed")
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
        assert "failed_order" in kinds
        assert "negative_balance" in kinds
