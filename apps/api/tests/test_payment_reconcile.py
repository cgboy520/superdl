"""Payment reconciliation loop: the order-query poller converges lost callbacks, manual backfill
(channel-verified), the anomaly list; repeated runs never double-credit."""

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
        """Paid on the channel but the callback was lost → the poller credits via order query;
        repeated runs never double-credit."""
        headers = await user_headers(client, "u13700000021@test.local")
        order = await create_order(client, headers, "66.00")
        MockChannel.mark_paid(order["order_no"], "txn-lost-1", "66.00")
        await _backdate_order(sm, order["order_no"], 2)

        assert await reconcile_pending_orders(sm) == 1
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "66.00"

        assert await reconcile_pending_orders(sm) == 0
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "66.00"

    async def test_unpaid_order_untouched(self, client: AsyncClient, sm):
        headers = await user_headers(client, "u13700000022@test.local")
        order = await create_order(client, headers, "10.00")
        await _backdate_order(sm, order["order_no"], 2)
        assert await reconcile_pending_orders(sm) == 0
        detail = (
            await client.get(f"/api/v1/wallet/recharges/{order['order_no']}", headers=headers)
        ).json()
        assert detail["status"] == "pending"

    async def test_failed_order_recovered_by_poller(self, client: AsyncClient, sm):
        """A failed order (mis-transitioned intermediate state) actually paid on the channel → the
        poller credits; repeated runs are idempotent."""
        headers = await user_headers(client, "u13700000042@test.local")
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
        assert await reconcile_pending_orders(sm) == 0
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "33.00"

    async def test_closed_order_recovered_by_poller(self, client: AsyncClient, sm):
        """Paid at the last moment before close with the callback lost: closed orders are part of
        the
        convergence scan, the poller rescues them."""
        headers = await user_headers(client, "u13700000044@test.local")
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
        assert await reconcile_pending_orders(sm) == 0
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "55.00"

    async def test_stale_closed_order_outside_window_not_scanned(self, client: AsyncClient, sm):
        """Old closed orders whose expires_at is beyond 48 h are not queried."""
        headers = await user_headers(client, "u13700000046@test.local")
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
        await _backdate_order(sm, order["order_no"], 2)
        assert await reconcile_pending_orders(sm) == 0
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "0.00"

    async def test_stale_failed_order_outside_window_not_scanned(self, client: AsyncClient, sm):
        """Old failed orders outside the 48 h window are not queried."""
        headers = await user_headers(client, "u13700000043@test.local")
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
        """The advisory lock is held (another replica is running) → this round yields."""
        from app.core.locks import LockKey, try_advisory_lock

        async with sm() as session, try_advisory_lock(session, LockKey.PAYMENT_RECONCILE) as got:
            assert got
            assert await reconcile_pending_orders(sm) == 0

    async def test_channel_unreachable_skipped(self, client: AsyncClient, sm):
        """Channel unreachable (e.g. credentials missing) → skip the order, retry next round,
        without blocking the others."""
        headers = await user_headers(client, "u13700000027@test.local")
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
        """A single credit failure does not end the round."""
        headers_bad = await user_headers(client, "u13700000031@test.local")
        bad = await create_order(client, headers_bad, "66.00")
        headers_good = await user_headers(client, "u13700000032@test.local")
        good = await create_order(client, headers_good, "20.00")
        MockChannel.mark_paid(bad["order_no"], "txn-bad-amount", "65.90")
        MockChannel.mark_paid(good["order_no"], "txn-good", "20.00")
        await _backdate_order(sm, bad["order_no"], 2)
        await _backdate_order(sm, good["order_no"], 2)

        assert await reconcile_pending_orders(sm) == 1
        bad_detail = (
            await client.get(f"/api/v1/wallet/recharges/{bad['order_no']}", headers=headers_bad)
        ).json()
        assert bad_detail["status"] == "pending"
        w = (await client.get("/api/v1/wallet", headers=headers_good)).json()
        assert w["balance"] == "20.00"


class TestBackfill:
    async def test_backfill_closed_order_after_verify(self, client: AsyncClient, sm):
        """Closed on timeout but paid on the channel: verify → backfill credits; a repeated backfill
        is
        refused."""
        headers = await user_headers(client, "u13700000023@test.local")
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
            json={"reason": "callback lost, confirmed by order query, backfilling"},
            headers=ah,
        )
        assert resp.status_code == 200, resp.text
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "88.00"

        resp = await client.post(
            f"/api/admin/v1/finance/orders/{order['order_no']}/backfill",
            json={"reason": "repeated operation"},
            headers=ah,
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "CONFLICT"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "88.00"

    async def test_backfill_idempotency_key_replay(self, client: AsyncClient, sm):
        """Backfill supports Idempotency-Key: a same-key replay returns the current state instead of
        409; a keyless retry stays 409."""
        headers = await user_headers(client, "u13700000041@test.local")
        order = await create_order(client, headers, "66.00")
        MockChannel.mark_paid(order["order_no"], "txn-backfill-idem", "66.00")

        ah = await admin_headers(sm, client, role="finance")
        keyed = {**ah, "Idempotency-Key": "backfill-20260822-01"}
        r1 = await client.post(
            f"/api/admin/v1/finance/orders/{order['order_no']}/backfill",
            json={"reason": "callback lost"},
            headers=keyed,
        )
        assert r1.status_code == 200, r1.text
        assert r1.json()["status"] == "paid"
        r2 = await client.post(
            f"/api/admin/v1/finance/orders/{order['order_no']}/backfill",
            json={"reason": "callback lost"},
            headers=keyed,
        )
        assert r2.status_code == 200, r2.text
        assert r2.headers["x-idempotent-replay"] == "true"
        assert r2.json()["order_no"] == order["order_no"]
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "66.00"

    async def test_backfill_key_reused_on_other_order_conflicts(self, client: AsyncClient, sm):
        """The same idempotency key used on another order: 409 naming the order holding the key."""
        headers = await user_headers(client, "u13700000042@test.local")
        order_a = await create_order(client, headers, "61.00")
        order_b = await create_order(client, headers, "62.00")
        MockChannel.mark_paid(order_a["order_no"], "txn-backfill-a", "61.00")
        MockChannel.mark_paid(order_b["order_no"], "txn-backfill-b", "62.00")

        ah = await admin_headers(sm, client, role="finance")
        keyed = {**ah, "Idempotency-Key": "backfill-shared-key"}
        r1 = await client.post(
            f"/api/admin/v1/finance/orders/{order_a['order_no']}/backfill",
            json={"reason": "first backfill"},
            headers=keyed,
        )
        assert r1.status_code == 200, r1.text
        r2 = await client.post(
            f"/api/admin/v1/finance/orders/{order_b['order_no']}/backfill",
            json={"reason": "same key, other order"},
            headers=keyed,
        )
        assert r2.status_code == 409
        assert r2.json()["message_key"] == "billing.backfillKeyInUse"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "61.00"

    async def test_backfill_refused_when_channel_unpaid(self, client: AsyncClient, sm):
        """Unpaid on the channel side → backfill refused."""
        headers = await user_headers(client, "u13700000024@test.local")
        order = await create_order(client, headers, "20.00")
        ah = await admin_headers(sm, client, role="finance")
        resp = await client.post(
            f"/api/admin/v1/finance/orders/{order['order_no']}/backfill",
            json={"reason": "user claims to have paid"},
            headers=ah,
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "PAYMENT_CHANNEL_ERROR"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "0.00"

    async def test_backfill_failed_order_after_verify(self, client: AsyncClient, sm):
        """A failed order verified as paid on the channel can be backfilled too."""
        headers = await user_headers(client, "u13700000029@test.local")
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
            json={"reason": "order failed locally but paid on the channel side"},
            headers=ah,
        )
        assert resp.status_code == 200, resp.text
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "11.00"

    async def test_backfill_refused_on_amount_mismatch(self, client: AsyncClient, sm):
        headers = await user_headers(client, "u13700000025@test.local")
        order = await create_order(client, headers, "30.00")
        MockChannel.mark_paid(order["order_no"], "txn-mismatch", "29.90")
        ah = await admin_headers(sm, client, role="finance")
        resp = await client.post(
            f"/api/admin/v1/finance/orders/{order['order_no']}/backfill",
            json={"reason": "verification"},
            headers=ah,
        )
        assert resp.status_code == 400
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "0.00"


class TestAnomalies:
    async def test_four_kinds_listed(self, client: AsyncClient, sm):
        headers = await user_headers(client, "u13700000026@test.local")
        stale = await create_order(client, headers, "15.00")
        await _backdate_order(sm, stale["order_no"], 15)
        closed = await create_order(client, headers, "25.00")
        failed = await create_order(client, headers, "35.00")
        await client.get("/api/v1/wallet", headers=headers)
        async with sm() as session:
            await session.execute(
                update(Order).where(Order.order_no == closed["order_no"]).values(status="closed")
            )
            await session.execute(
                update(Order).where(Order.order_no == failed["order_no"]).values(status="failed")
            )
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
