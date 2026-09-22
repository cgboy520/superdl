"""Subscriptions (prepaid) end to end: discount arithmetic, idempotent zero double charging,
settlement skip, matching filters, stock reservation, expiry chain."""

# pyright: reportPrivateUsage=false

import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app.core.platform_config import (
    RuntimeConfig,
    get_runtime_config,
    runtime_config_from_strings,
)
from app.core.pricing import (
    MARKET_SUBSCRIPTION,
    price_for,
    quote_subscription,
)
from app.core.timeutil import now_utc
from app.modules.billing import wallet
from app.modules.billing.models import BalanceLedger, BillHourly, Subscription, Wallet
from app.modules.billing.patrol import balance_patrol
from app.modules.billing.subscriptions import subscription_patrol
from app.modules.catalog.models import Sku
from app.modules.notify.models import Notification
from app.modules.orchestrator.models import Instance
from app.modules.orchestrator.reconciler import reconcile_once
from tests.helpers import (
    IMAGE_PYTORCH,
    buy_subscription,
    create_test_sku,
    drain,
    fund_wallet,
    funded_user,
    get_instance,
    provision_running,
    provision_subscription,
    seed_node_spec,
)

pytestmark = pytest.mark.usefixtures("fake")

IMAGE = IMAGE_PYTORCH


class TestExpiringEndpoint:
    async def test_expiring_lists_only_horizon_hits_sorted(self, client, sm, fake):
        """Expiry banner endpoint: only expiring (active and ≤ within_days) instances, ascending, no
        pagination;
        /instances/expiring is not swallowed by /instances/{uuid}."""
        headers, uuid, _user_id, _sku, _key = await provision_subscription(
            client, sm, fake, "u13910000101@test.local"
        )
        async with sm() as session:
            await session.execute(
                update(Subscription)
                .where(
                    Subscription.instance_id
                    == select(Instance.id).where(Instance.uuid == uuid).scalar_subquery()
                )
                .values(expires_at=now_utc() + timedelta(days=3))
            )
            await session.commit()
        resp = await client.get("/api/v1/instances/expiring?within_days=7", headers=headers)
        assert resp.status_code == 200, resp.text
        items = resp.json()
        assert isinstance(items, list)
        assert [i["uuid"] for i in items] == [uuid]
        assert items[0]["subscription"]["status"] == "active"
        resp = await client.get("/api/v1/instances/expiring?within_days=1", headers=headers)
        assert resp.json() == []


async def _policies(sm) -> RuntimeConfig:
    async with sm() as session:
        return await get_runtime_config(session)


class TestQuoteArithmetic:
    """The quote triple and the actual charge are consistent."""

    def test_month_matches_hand_math(self):
        """The monthly price is the hourly price × 720 hours × the monthly discount."""
        policies = runtime_config_from_strings({"period_discount_month": "80"})
        q = quote_subscription(
            Decimal("3.9900"), gpu_count=1, period="month", period_count=1, policies=policies
        )
        assert q.hours == 720
        assert q.unit_price == Decimal("3.1920")
        assert q.list_amount == Decimal("2872.80")
        assert q.amount == Decimal("2298.24")
        assert q.discount_amount == Decimal("574.56")

    def test_cpu_instance_bills_one_unit(self):
        """A CPU instance with gpu_count=0 charges 1 unit (billing_units)."""
        policies = runtime_config_from_strings({"period_discount_day": "100"})
        q = quote_subscription(
            Decimal("0.5000"), gpu_count=0, period="day", period_count=1, policies=policies
        )
        assert q.amount == Decimal("12.00")


class TestOrderAndIdempotency:
    async def test_order_debits_once_and_snapshots_discounted_price(self, client, sm, fake):
        """The order debits the whole period amount and the instance stores the discounted hourly
        price."""
        _headers, uuid, user_id, _, _ = await provision_subscription(
            client, sm, fake, "u13911100001@test.local"
        )
        policies = await _policies(sm)
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
            sub = (
                await s.execute(select(Subscription).where(Subscription.instance_id == inst.id))
            ).scalar_one()
            ledger = (
                (
                    await s.execute(
                        select(BalanceLedger).where(
                            BalanceLedger.user_id == user_id,
                            BalanceLedger.ref_type == "subscription",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert inst.market == MARKET_SUBSCRIPTION
        assert inst.price_hourly == price_for(
            sub.unit_price, market=MARKET_SUBSCRIPTION, policies=policies, period="month"
        )
        assert len(ledger) == 1
        assert ledger[0].amount == -sub.amount_paid
        assert ledger[0].ref_id == str(sub.id)
        assert sub.expires_at - sub.started_at == timedelta(hours=720)

    async def test_replayed_key_charges_once(self, client, sm, fake):
        """Replaying the same Idempotency-Key: zero double charging, zero duplicate subscription
        rows."""
        headers, user_id, key_id = await funded_user(
            client, sm, "u13911100002@test.local", "5000.00"
        )
        sku_id = await create_test_sku(sm)
        await seed_node_spec(sm, node_name="node-idem")

        code, first = await buy_subscription(client, headers, sku_id, key_id, idem="k-1")
        assert code == 202
        code2, second = await buy_subscription(client, headers, sku_id, key_id, idem="k-1")
        assert code2 == 200
        assert second["uuid"] == first["uuid"]

        async with sm() as s:
            subs = (
                (await s.execute(select(Subscription).where(Subscription.user_id == user_id)))
                .scalars()
                .all()
            )
            balance = await wallet.get_balance(s, user_id)
        assert len(subs) == 1
        assert balance == Decimal("5000.00") - subs[0].amount_paid

    async def test_insufficient_balance_never_reaches_creating(self, client, sm, fake):
        """Balance short of one month: 400, no creating instance left behind, no debit."""
        headers, user_id, key_id = await funded_user(client, sm, "u13911100003@test.local", "10.00")
        sku_id = await create_test_sku(sm)
        await seed_node_spec(sm, node_name="node-poor")

        code, body = await buy_subscription(client, headers, sku_id, key_id)
        assert code == 400
        assert body["code"] == "INSUFFICIENT_BALANCE"
        async with sm() as s:
            count = len(
                (await s.execute(select(Instance).where(Instance.user_id == user_id)))
                .scalars()
                .all()
            )
            assert await wallet.get_balance(s, user_id) == Decimal("10.00")
        assert count == 0

    async def test_period_field_rejected_on_on_demand(self, client, sm):
        """An on-demand order with period is always 422."""
        headers, _user_id, key_id = await funded_user(client, sm, "u13911100004@test.local")
        sku_id = await create_test_sku(sm)
        resp = await client.post(
            "/api/v1/instances",
            json={
                "sku_id": sku_id,
                "gpu_count": 1,
                "image_ref": IMAGE,
                "ssh_key_ids": [key_id],
                "period": "month",
            },
            headers=headers,
        )
        assert resp.status_code == 422

    async def test_sku_with_period_disabled_refuses_subscription(self, client, sm, fake):
        """With subscriptions disabled on the SKU a direct API call cannot buy one either."""
        headers, _user_id, key_id = await funded_user(
            client, sm, "u13911100005@test.local", "5000.00"
        )
        sku_id = await create_test_sku(sm, gpu_cores_pct=45, vcpu=7)
        await seed_node_spec(sm, node_name="node-nop")
        async with sm() as s:
            await s.execute(update(Sku).where(Sku.id == sku_id).values(period_enabled=False))
            await s.commit()
        code, body = await buy_subscription(client, headers, sku_id, key_id)
        assert code == 400
        assert body["message_key"] == "orchestrator.periodNotEnabled"


class TestSettlementSkip:
    async def test_no_hourly_bill_for_subscription(self, client, sm, fake):
        """A subscription instance produces no bills_hourly rows, running or stopped."""
        from app.core.timeutil import hour_floor
        from app.modules.billing.settlement import settle_due_hours
        from app.modules.orchestrator.models import InstanceEvent

        headers, uuid, _user_id, _, _ = await provision_subscription(
            client, sm, fake, "u13911100010@test.local"
        )
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
            await s.execute(
                update(InstanceEvent)
                .where(InstanceEvent.instance_id == inst.id)
                .values(created_at=hour_floor(now_utc()) - timedelta(hours=2))
            )
            await s.commit()
            instance_id = inst.id
        await settle_due_hours(sm)
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        async with sm() as s:
            bills = (
                (await s.execute(select(BillHourly).where(BillHourly.instance_id == instance_id)))
                .scalars()
                .all()
            )
        assert bills == []


class TestCompanionFilters:
    async def test_zero_balance_subscriber_is_not_stopped(self, client, sm, fake):
        """A monthly user with a zero balance is not stopped by the arrears patrol."""
        _headers, uuid, user_id, _, _ = await provision_subscription(
            client, sm, fake, "u13911100020@test.local"
        )
        async with sm() as s:
            w = await wallet.lock_wallet(s, user_id)
            w.balance = Decimal("0.00")
            await s.commit()
        await balance_patrol(sm)
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
        assert inst.status == "running"

    async def test_subscription_does_not_block_new_on_demand_instance(self, client, sm, fake):
        """Monthly instances do not enter the in-flight burn rate."""
        headers, _uuid, user_id, sku_id, key_id = await provision_subscription(
            client, sm, fake, "u13911100021@test.local"
        )
        async with sm() as s:
            w = await wallet.lock_wallet(s, user_id)
            w.balance = Decimal("50.00")
            await s.commit()
        resp = await client.post(
            "/api/v1/instances",
            json={
                "sku_id": sku_id,
                "gpu_count": 1,
                "image_ref": IMAGE,
                "ssh_key_ids": [key_id],
            },
            headers=headers,
        )
        assert resp.status_code == 202, resp.text

    async def test_stopped_subscription_is_not_frozen_by_arrears(self, client, sm, fake):
        """Stopped within the period + balance 0: no entry into the arrears freeze chain."""
        headers, uuid, user_id, _, _ = await provision_subscription(
            client, sm, fake, "u13911100022@test.local"
        )
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        async with sm() as s:
            w = await wallet.lock_wallet(s, user_id)
            w.balance = Decimal("0.00")
            await s.commit()
        await balance_patrol(sm)
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
        assert inst.status == "stopped"
        assert inst.frozen_deadline is None


class TestCapacityReservation:
    async def test_stopped_subscription_still_holds_capacity(self, client, sm, fake):
        """An unexpired subscription instance keeps occupying stock even when stopped."""
        headers, uuid, _user_id, sku_id, _key_id = await provision_subscription(
            client,
            sm,
            fake,
            "u13911100030@test.local",
            sku={"gpu_cores_pct": 100, "vcpu": 16, "mem_gb": 64},
        )
        async with sm() as s:
            from app.modules.nodes.models import NodeSpec

            await s.execute(update(NodeSpec).values(gpu_count=1, gpu_used=0))
            await s.commit()
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)

        other_headers, _other_uid, other_key = await funded_user(
            client, sm, "u13911100031@test.local", "5000.00"
        )
        resp = await client.post(
            "/api/v1/instances",
            json={
                "sku_id": sku_id,
                "gpu_count": 1,
                "image_ref": IMAGE,
                "ssh_key_ids": [other_key],
            },
            headers=other_headers,
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "NO_CAPACITY"
        market = (await client.get("/api/v1/skus")).json()
        assert next(s["available_count"] for s in market if s["id"] == sku_id) == 0


class TestRenewal:
    async def test_renew_extends_from_old_expiry_and_chains(self, client, sm, fake):
        """An early renewal counts from the old expiry and links renewed_from_id."""
        headers, uuid, user_id, _, _ = await provision_subscription(
            client, sm, fake, "u13911100040@test.local"
        )
        async with sm() as s:
            old = (
                await s.execute(select(Subscription).where(Subscription.user_id == user_id))
            ).scalar_one()
            old_expiry, old_id = old.expires_at, old.id

        resp = await client.post(
            f"/api/v1/instances/{uuid}/renew",
            json={"period": "month", "period_count": 2},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        quote = resp.json()["quote"]
        assert quote["period_count"] == 2
        assert quote["hours"] == 1440

        async with sm() as s:
            rows = (
                (
                    await s.execute(
                        select(Subscription)
                        .where(Subscription.user_id == user_id)
                        .order_by(Subscription.id)
                    )
                )
                .scalars()
                .all()
            )
        assert [r.status for r in rows] == ["expired", "active"]
        assert rows[1].renewed_from_id == old_id
        assert rows[1].started_at == old_expiry
        assert rows[1].expires_at == old_expiry + timedelta(hours=1440)

    async def test_renew_prices_from_original_snapshot_not_current_sku(self, client, sm, fake):
        """Renewal prices from subscriptions.unit_price (the list-price snapshot at order time), not
        the current SKU price."""
        headers, uuid, user_id, sku_id, _ = await provision_subscription(
            client, sm, fake, "u13911100041@test.local"
        )
        async with sm() as s:
            before = (
                await s.execute(select(Subscription).where(Subscription.user_id == user_id))
            ).scalar_one()
            paid_before = before.amount_paid
            await s.execute(
                update(Sku).where(Sku.id == sku_id).values(price_hourly=Decimal("99.0000"))
            )
            await s.commit()
        resp = await client.post(
            f"/api/v1/instances/{uuid}/renew",
            json={"period": "month", "period_count": 1},
            headers=headers,
        )
        assert resp.status_code == 200
        assert Decimal(resp.json()["quote"]["amount"]) == paid_before

    async def test_renew_is_idempotent(self, client, sm, fake):
        """Renewal replayed with the same Idempotency-Key: zero double charging."""
        headers, uuid, user_id, _, _ = await provision_subscription(
            client, sm, fake, "u13911100042@test.local"
        )
        body = {"period": "week", "period_count": 1}
        h = {**headers, "Idempotency-Key": "renew-1"}
        first = await client.post(f"/api/v1/instances/{uuid}/renew", json=body, headers=h)
        second = await client.post(f"/api/v1/instances/{uuid}/renew", json=body, headers=h)
        assert first.status_code == 200 and second.status_code == 200
        async with sm() as s:
            rows = (
                (await s.execute(select(Subscription).where(Subscription.user_id == user_id)))
                .scalars()
                .all()
            )
            consume = (
                (
                    await s.execute(
                        select(BalanceLedger).where(
                            BalanceLedger.user_id == user_id,
                            BalanceLedger.ref_type == "subscription",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 2
        assert len(consume) == 2

    async def test_renew_rejects_on_demand_instance(self, client, sm, fake):
        headers, uuid, _ = await provision_running(
            client, sm, fake, email="u13911100043@test.local"
        )
        resp = await client.post(
            f"/api/v1/instances/{uuid}/renew",
            json={"period": "month", "period_count": 1},
            headers=headers,
        )
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "orchestrator.renewNotSubscription"


class TestIdempotencyFingerprint:
    """Subscription idempotency keys carry a request fingerprint: same key with different params →
    409, never a silent replay of another order."""

    async def test_same_key_on_another_instance_is_409(self, client, sm, fake):
        """The same key aimed at another instance → 409."""
        headers, uuid_a, user_id, sku_id, key_id = await provision_subscription(
            client, sm, fake, "u13911100050@test.local", fund="20000.00"
        )
        code, created = await buy_subscription(client, headers, sku_id, key_id)
        assert code == 202, created
        uuid_b = created["uuid"]

        body = {"period": "week", "period_count": 1}
        h = {**headers, "Idempotency-Key": "shared-renew-key"}
        first = await client.post(f"/api/v1/instances/{uuid_a}/renew", json=body, headers=h)
        assert first.status_code == 200, first.text

        async with sm() as s:
            inst_b = (await s.execute(select(Instance).where(Instance.uuid == uuid_b))).scalar_one()
            sub_b_before = (
                await s.execute(select(Subscription).where(Subscription.instance_id == inst_b.id))
            ).scalar_one()
            expiry_before, balance_before = (
                sub_b_before.expires_at,
                await wallet.get_balance(s, user_id),
            )

        crossed = await client.post(f"/api/v1/instances/{uuid_b}/renew", json=body, headers=h)
        assert crossed.status_code == 409, crossed.text
        assert crossed.json()["message_key"] == "common.idempotencyKeyMismatch"
        async with sm() as s:
            rows = (
                (await s.execute(select(Subscription).where(Subscription.instance_id == inst_b.id)))
                .scalars()
                .all()
            )
            assert [r.expires_at for r in rows] == [expiry_before]
            assert await wallet.get_balance(s, user_id) == balance_before

    async def test_same_key_with_another_period_is_409(self, client, sm, fake):
        """Same instance, same key, different period → 409: the fingerprint covers every parameter
        that shapes the order."""
        headers, uuid, _, _, _ = await provision_subscription(
            client, sm, fake, "u13911100051@test.local", fund="20000.00"
        )
        h = {**headers, "Idempotency-Key": "period-swap-key"}
        first = await client.post(
            f"/api/v1/instances/{uuid}/renew",
            json={"period": "week", "period_count": 1},
            headers=h,
        )
        assert first.status_code == 200, first.text
        swapped = await client.post(
            f"/api/v1/instances/{uuid}/renew",
            json={"period": "month", "period_count": 1},
            headers=h,
        )
        assert swapped.status_code == 409
        assert swapped.json()["message_key"] == "common.idempotencyKeyMismatch"

    async def test_convert_replay_lookup_rejects_another_instance(self, client, sm, fake):
        """The conversion path's replay lookup (find_replay_row): same key, different params → 409,
        the replay row is returned only when every parameter matches."""
        from app.core.errors import AppError
        from app.modules.billing import subscriptions

        headers, uuid, user_id = await provision_running(
            client, sm, fake, email="u13911100052@test.local"
        )
        await fund_wallet(sm, user_id, "20000.00")
        body = {"period": "week", "period_count": 1}
        h = {**headers, "Idempotency-Key": "conv-key"}
        resp = await client.post(f"/api/v1/instances/{uuid}/subscribe", json=body, headers=h)
        assert resp.status_code == 200, resp.text

        async with sm() as s:
            converted = (
                await s.execute(select(Instance).where(Instance.uuid == uuid))
            ).scalar_one()
            hit = await subscriptions.find_replay_row(
                s,
                user_id=user_id,
                key="conv-key",
                instance_id=converted.id,
                period="week",
                period_count=1,
            )
            assert hit is not None and hit.instance_id == converted.id
            with pytest.raises(AppError) as exc:
                await subscriptions.find_replay_row(
                    s,
                    user_id=user_id,
                    key="conv-key",
                    instance_id=converted.id + 10_000,
                    period="week",
                    period_count=1,
                )
            assert exc.value.message_key == "common.idempotencyKeyMismatch"
            with pytest.raises(AppError):
                await subscriptions.find_replay_row(
                    s,
                    user_id=user_id,
                    key="conv-key",
                    instance_id=converted.id,
                    period="month",
                    period_count=1,
                )


class TestExpiryChain:
    async def test_frozen_subscription_is_not_unfrozen_by_balance(self, client, sm, fake):
        """An instance frozen on expiry does not unfreeze because the balance suffices; renewal is
        the unfreeze condition."""
        _headers, uuid, user_id, _, _ = await provision_subscription(
            client, sm, fake, "u13911100051@test.local"
        )
        async with sm() as s:
            await s.execute(
                update(Subscription)
                .where(Subscription.user_id == user_id)
                .values(expires_at=now_utc() - timedelta(minutes=1))
            )
            await s.commit()
        await subscription_patrol(sm)
        await drain(sm)
        await reconcile_once(sm)
        await subscription_patrol(sm)
        await balance_patrol(sm)
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
        assert inst.status == "frozen"

    async def test_expired_subscription_cannot_start(self, client, sm, fake):
        """Starting after expiry is refused (renew first)."""
        headers, uuid, user_id, _, _ = await provision_subscription(
            client, sm, fake, "u13911100052@test.local"
        )
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        async with sm() as s:
            await s.execute(
                update(Subscription)
                .where(Subscription.user_id == user_id)
                .values(expires_at=now_utc() - timedelta(minutes=1))
            )
            await s.commit()
        resp = await client.post(f"/api/v1/instances/{uuid}/start", headers=headers)
        assert resp.status_code == 409
        assert resp.json()["code"] == "SUBSCRIPTION_EXPIRED"

    async def test_renew_unfreezes(self, client, sm, fake):
        """Renewing while frozen unfreezes (back to stopped, the user starts it)."""
        headers, uuid, user_id, _, _ = await provision_subscription(
            client, sm, fake, "u13911100053@test.local"
        )
        async with sm() as s:
            await s.execute(
                update(Subscription)
                .where(Subscription.user_id == user_id)
                .values(expires_at=now_utc() - timedelta(minutes=1))
            )
            await s.commit()
        await subscription_patrol(sm)
        await drain(sm)
        await reconcile_once(sm)
        await subscription_patrol(sm)

        resp = await client.post(
            f"/api/v1/instances/{uuid}/renew",
            json={"period": "month", "period_count": 1},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["instance"]["status"] == "stopped"
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
        assert inst.frozen_deadline is None

    async def test_auto_renew_charges_and_extends(self, client, sm, fake):
        """Auto-renewal: charged and extended at the expiry instant, the instance keeps running."""
        headers, uuid, user_id, _, _ = await provision_subscription(
            client, sm, fake, "u13911100054@test.local"
        )
        resp = await client.post(
            f"/api/v1/instances/{uuid}/auto-renew", json={"enabled": True}, headers=headers
        )
        assert resp.status_code == 200
        async with sm() as s:
            await s.execute(
                update(Subscription)
                .where(Subscription.user_id == user_id)
                .values(expires_at=now_utc() - timedelta(minutes=1))
            )
            await s.commit()
        counts = await subscription_patrol(sm)
        assert counts["renewed"] == 1
        assert counts["stopped"] == 0
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
            rows = (
                (await s.execute(select(Subscription).where(Subscription.user_id == user_id)))
                .scalars()
                .all()
            )
        assert inst.status == "running"
        assert len(rows) == 2
        assert rows[-1].auto_renew is True

    async def test_auto_renew_without_balance_falls_back_to_stop(self, client, sm, fake):
        """Auto-renewal with insufficient balance: no overdraft, the expiry stop chain runs, and the
        old subscription row is not corrupted."""
        headers, uuid, user_id, _, _ = await provision_subscription(
            client, sm, fake, "u13911100055@test.local"
        )
        await client.post(
            f"/api/v1/instances/{uuid}/auto-renew", json={"enabled": True}, headers=headers
        )
        async with sm() as s:
            w = await wallet.lock_wallet(s, user_id)
            w.balance = Decimal("1.00")
            await s.execute(
                update(Subscription)
                .where(Subscription.user_id == user_id)
                .values(expires_at=now_utc() - timedelta(minutes=1))
            )
            await s.commit()
        counts = await subscription_patrol(sm)
        assert counts["renew_failed"] == 1
        assert counts["stopped"] == 1
        async with sm() as s:
            rows = (
                (await s.execute(select(Subscription).where(Subscription.user_id == user_id)))
                .scalars()
                .all()
            )
            assert await wallet.get_balance(s, user_id) == Decimal("1.00")
        assert len(rows) == 1
        assert rows[0].status == "expired"

    async def test_renew_rejected_when_balance_is_frozen(self, client, sm, fake):
        """Manual renewal judges by available balance (balance − frozen); reversal-frozen amounts
        are
        unavailable."""
        headers, uuid, user_id, _, _ = await provision_subscription(
            client, sm, fake, "u13911100057@test.local"
        )
        async with sm() as s:
            paid = (
                (await s.execute(select(Subscription).where(Subscription.user_id == user_id)))
                .scalar_one()
                .amount_paid
            )
            w = await wallet.lock_wallet(s, user_id)
            w.frozen = w.balance - paid + Decimal("0.01")
            await s.commit()
        resp = await client.post(
            f"/api/v1/instances/{uuid}/renew",
            json={"period": "month", "period_count": 1},
            headers=headers,
        )
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "billing.insufficientAvailableFrozen"
        async with sm() as s:
            rows = (
                (await s.execute(select(Subscription).where(Subscription.user_id == user_id)))
                .scalars()
                .all()
            )
            assert [r.status for r in rows] == ["active"]

    async def test_auto_renew_with_frozen_balance_falls_back_to_stop(self, client, sm, fake):
        """Auto-renewal hitting a full freeze: the precheck by available balance records
        renew_failed
        and takes the expiry stop."""
        headers, uuid, user_id, _, _ = await provision_subscription(
            client, sm, fake, "u13911100058@test.local"
        )
        await client.post(
            f"/api/v1/instances/{uuid}/auto-renew", json={"enabled": True}, headers=headers
        )
        async with sm() as s:
            w = await wallet.lock_wallet(s, user_id)
            w.frozen = w.balance
            await s.execute(
                update(Subscription)
                .where(Subscription.user_id == user_id)
                .values(expires_at=now_utc() - timedelta(minutes=1))
            )
            await s.commit()
        counts = await subscription_patrol(sm)
        assert counts["renew_failed"] == 1
        assert counts["stopped"] == 1
        async with sm() as s:
            w2 = await wallet.lock_wallet(s, user_id)
            assert w2.balance > Decimal("0.00")
            assert w2.frozen == w2.balance
            rows = (
                (await s.execute(select(Subscription).where(Subscription.user_id == user_id)))
                .scalars()
                .all()
            )
        assert len(rows) == 1
        assert rows[0].status == "expired"

    async def test_expiring_warning_is_sent_once(self, client, sm, fake):
        """The expiry warning is sent once per expiry instant."""
        from app.modules.notify.models import Notification

        _headers, _uuid, user_id, _, _ = await provision_subscription(
            client, sm, fake, "u13911100056@test.local"
        )
        async with sm() as s:
            await s.execute(
                update(Subscription)
                .where(Subscription.user_id == user_id)
                .values(expires_at=now_utc() + timedelta(hours=12))
            )
            await s.commit()
        assert (await subscription_patrol(sm))["warned"] == 1
        assert (await subscription_patrol(sm))["warned"] == 0
        async with sm() as s:
            notes = (
                (
                    await s.execute(
                        select(Notification).where(
                            Notification.user_id == user_id, Notification.type == "subscription"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(notes) == 1


class TestRenewConcurrency:
    """Concurrent renewal (manual × automatic) never double-charges."""

    async def test_concurrent_manual_and_auto_renew_never_double_charges(self, client, sm, fake):
        """Whoever wins: one active row per instance, the same period is not renewed twice, balance
        and chain charges are consistent."""
        headers, uuid, user_id, _, _ = await provision_subscription(
            client, sm, fake, "u13911100096@test.local"
        )
        async with sm() as s:
            await s.execute(
                update(Subscription)
                .where(Subscription.user_id == user_id)
                .values(auto_renew=True, expires_at=now_utc() - timedelta(minutes=1))
            )
            await s.commit()
            balance_before = await wallet.get_balance(s, user_id)

        async def manual() -> None:
            resp = await client.post(
                f"/api/v1/instances/{uuid}/renew",
                json={"period": "month", "period_count": 1},
                headers=headers,
            )
            assert resp.status_code == 200, resp.text

        await asyncio.gather(manual(), subscription_patrol(sm))

        async with sm() as s:
            rows = list(
                (await s.execute(select(Subscription).where(Subscription.user_id == user_id)))
                .scalars()
                .all()
            )
            active = [r for r in rows if r.status == "active"]
            assert len(active) == 1
            parents = [r.renewed_from_id for r in rows if r.renewed_from_id is not None]
            assert len(parents) == len(set(parents))
            charged = sum(r.amount_paid for r in rows if r.renewed_from_id is not None)
            assert await wallet.get_balance(s, user_id) == balance_before - charged

    async def test_auto_renew_after_manual_renew_is_noop(self, client, sm, fake):
        """The expiry patrol after a manual renewal: auto-renewal does not charge again."""
        headers, uuid, user_id, _, _ = await provision_subscription(
            client, sm, fake, "u13911100097@test.local"
        )
        async with sm() as s:
            await s.execute(
                update(Subscription)
                .where(Subscription.user_id == user_id)
                .values(auto_renew=True, expires_at=now_utc() - timedelta(minutes=1))
            )
            await s.commit()
        resp = await client.post(
            f"/api/v1/instances/{uuid}/renew",
            json={"period": "month", "period_count": 1},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        async with sm() as s:
            balance_after_manual = await wallet.get_balance(s, user_id)

        counts = await subscription_patrol(sm)
        assert counts["renewed"] == 0
        async with sm() as s:
            assert await wallet.get_balance(s, user_id) == balance_after_manual
            rows = list(
                (await s.execute(select(Subscription).where(Subscription.user_id == user_id)))
                .scalars()
                .all()
            )
            assert len([r for r in rows if r.status == "active"]) == 1


class TestRestartGate:
    """The restart money gate matches the start gate: subscriptions look at the period, on-demand at
    the balance."""

    async def test_subscription_restart_ignores_balance(self, client, sm, fake):
        """A monthly instance with balance 0 restarts as usual."""
        headers, uuid, user_id, _, _ = await provision_subscription(
            client, sm, fake, "u13911100090@test.local"
        )
        async with sm() as s:
            await s.execute(update(Wallet).where(Wallet.user_id == user_id).values(balance=0))
            await s.commit()
        resp = await client.post(f"/api/v1/instances/{uuid}/restart", headers=headers)
        assert resp.status_code == 200, resp.text
        await drain(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "starting"
        fake.mark_ready(f"tenant-{user_id}", uuid)
        await reconcile_once(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "running"

    async def test_expired_subscription_restart_aborts_at_stopped(self, client, sm, fake):
        """Restarting an expired monthly instance: stops at stopped with a notification."""
        headers, uuid, user_id, _, _ = await provision_subscription(
            client, sm, fake, "u13911100091@test.local"
        )
        async with sm() as s:
            await s.execute(
                update(Subscription)
                .where(Subscription.user_id == user_id)
                .values(expires_at=now_utc() - timedelta(minutes=1))
            )
            await s.commit()
        resp = await client.post(f"/api/v1/instances/{uuid}/restart", headers=headers)
        assert resp.status_code == 200, resp.text
        await drain(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "stopped"
        async with sm() as s:
            notice = (
                await s.execute(
                    select(Notification).where(
                        Notification.user_id == user_id,
                        Notification.dedup_key.like("restart_subscription_expired:%"),
                    )
                )
            ).scalar_one()
            assert "subscription expired" in notice.title

    async def test_payg_restart_insufficient_balance_aborts_at_stopped(self, client, sm, fake):
        """An on-demand instance short of balance: the restart aborts at stopped with a
        notification."""
        headers, uuid, user_id = await provision_running(
            client, sm, fake, "u13900000150@test.local"
        )
        async with sm() as s:
            await s.execute(update(Wallet).where(Wallet.user_id == user_id).values(balance=0))
            await s.commit()
        resp = await client.post(f"/api/v1/instances/{uuid}/restart", headers=headers)
        assert resp.status_code == 200, resp.text
        await drain(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "stopped"
        async with sm() as s:
            notice = (
                await s.execute(
                    select(Notification).where(
                        Notification.user_id == user_id,
                        Notification.dedup_key.like("restart_no_balance:%"),
                    )
                )
            ).scalar_one()
            assert "insufficient balance" in notice.title


class TestRelease:
    async def test_release_cancels_subscription_without_refund(self, client, sm, fake):
        """Release mid-period: the subscription becomes cancelled, no refund, the stock reservation
        is
        released."""
        headers, uuid, user_id, _, _ = await provision_subscription(
            client, sm, fake, "u13911100060@test.local"
        )
        async with sm() as s:
            balance_before = await wallet.get_balance(s, user_id)
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        resp = await client.delete(f"/api/v1/instances/{uuid}", headers=headers)
        assert resp.status_code == 200
        async with sm() as s:
            row = (
                await s.execute(select(Subscription).where(Subscription.user_id == user_id))
            ).scalar_one()
            assert await wallet.get_balance(s, user_id) == balance_before
        assert row.status == "cancelled"


class TestReconcileAndReporting:
    async def test_prepaid_is_reconciled_and_counted_as_revenue(self, client, sm, fake):
        """Prepaid debits (ref_type='subscription') enter reconciliation and the revenue
        statistics."""
        from app.modules.billing.reconcile import bills_vs_consume, dangling_consume_refs
        from app.modules.billing.wallet import revenue_summary

        _, _, user_id, _, _ = await provision_subscription(
            client, sm, fake, "u13911100080@test.local"
        )
        async with sm() as s:
            paid = (
                (await s.execute(select(Subscription).where(Subscription.user_id == user_id)))
                .scalar_one()
                .amount_paid
            )
            since = now_utc() - timedelta(days=1)
            billed, consumed = await bills_vs_consume(s, since, now_utc() + timedelta(minutes=1))
            dangling = await dangling_consume_refs(s)
            revenue = await revenue_summary(s)
        assert billed == consumed == paid
        assert dangling == 0
        assert Decimal(revenue["today_prepaid"]) == paid
        assert Decimal(revenue["month_revenue"]) >= paid

    async def test_overview_counts_active_subscriptions(self, client, sm, fake):
        """The overview "covered subscriptions" counts subscription rows; a stopped monthly instance
        is still covered."""
        from app.modules.adminapi.overview import overview

        headers, uuid, _, _, _ = await provision_subscription(
            client, sm, fake, "u13911100081@test.local"
        )
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        async with sm() as s:
            assert (await overview(s))["subscriptions_active"] == 1


class TestConvertToSubscription:
    """On-demand → subscription: no double or missing bills on either side of the conversion
    point."""

    async def _on_demand_running(self, client, sm, fake, email: str):
        headers, uuid, user_id = await provision_running(client, sm, fake, email=email)
        await fund_wallet(sm, user_id, "5000.00")
        return headers, uuid, user_id

    async def test_pre_conversion_hours_are_settled_not_forgiven(self, client, sm, fake):
        """The on-demand stretch before the conversion is billed first."""
        from app.core.timeutil import hour_floor
        from app.modules.orchestrator.models import InstanceEvent

        headers, uuid, _user_id = await self._on_demand_running(
            client, sm, fake, "u13911100090@test.local"
        )
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
            instance_id, unit = inst.id, inst.price_hourly
            await s.execute(
                update(InstanceEvent)
                .where(InstanceEvent.instance_id == instance_id)
                .values(created_at=hour_floor(now_utc()))
            )
            await s.commit()

        resp = await client.post(
            f"/api/v1/instances/{uuid}/subscribe",
            json={"period": "month", "period_count": 1},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        async with sm() as s:
            bills = (
                (await s.execute(select(BillHourly).where(BillHourly.instance_id == instance_id)))
                .scalars()
                .all()
            )
            inst = (
                await s.execute(select(Instance).where(Instance.id == instance_id))
            ).scalar_one()
        assert len(bills) == 1
        assert bills[0].unit_price == unit
        assert bills[0].detail["source"] == "convert"
        assert bills[0].seconds_used > 0
        assert inst.market == "subscription"
        assert inst.price_hourly < unit

    async def test_after_conversion_settlement_skips_the_instance(self, client, sm, fake):
        """Hourly settlement leaves it alone after the conversion."""
        from app.core.timeutil import hour_floor
        from app.modules.billing.settlement import settle_due_hours
        from app.modules.orchestrator.models import InstanceEvent

        headers, uuid, _user_id = await self._on_demand_running(
            client, sm, fake, "u13911100091@test.local"
        )
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
            instance_id = inst.id
            await s.execute(
                update(InstanceEvent)
                .where(InstanceEvent.instance_id == instance_id)
                .values(created_at=hour_floor(now_utc()) - timedelta(hours=2))
            )
            await s.commit()
        await client.post(
            f"/api/v1/instances/{uuid}/subscribe",
            json={"period": "month", "period_count": 1},
            headers=headers,
        )
        async with sm() as s:
            before = len(
                (await s.execute(select(BillHourly).where(BillHourly.instance_id == instance_id)))
                .scalars()
                .all()
            )
        await settle_due_hours(sm)
        async with sm() as s:
            after = len(
                (await s.execute(select(BillHourly).where(BillHourly.instance_id == instance_id)))
                .scalars()
                .all()
            )
        assert after == before

    async def test_convert_charges_once_under_replay(self, client, sm, fake):
        """Replaying the same Idempotency-Key: zero double charging, zero duplicate subscription
        rows."""
        headers, uuid, user_id = await self._on_demand_running(
            client, sm, fake, "u13911100092@test.local"
        )
        h = {**headers, "Idempotency-Key": "conv-1"}
        body = {"period": "week", "period_count": 1}
        first = await client.post(f"/api/v1/instances/{uuid}/subscribe", json=body, headers=h)
        second = await client.post(f"/api/v1/instances/{uuid}/subscribe", json=body, headers=h)
        assert first.status_code == 200 and second.status_code == 200, second.text
        async with sm() as s:
            subs = (
                (await s.execute(select(Subscription).where(Subscription.user_id == user_id)))
                .scalars()
                .all()
            )
            entries = (
                (
                    await s.execute(
                        select(BalanceLedger).where(
                            BalanceLedger.user_id == user_id,
                            BalanceLedger.ref_type == "subscription",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(subs) == 1
        assert len(entries) == 1

    async def test_convert_twice_without_key_is_refused(self, client, sm, fake):
        """Converting again without an idempotency key: the second attempt is refused, no second
        order."""
        headers, uuid, _user_id = await self._on_demand_running(
            client, sm, fake, "u13911100093@test.local"
        )
        body = {"period": "week", "period_count": 1}
        assert (
            await client.post(f"/api/v1/instances/{uuid}/subscribe", json=body, headers=headers)
        ).status_code == 200
        second = await client.post(
            f"/api/v1/instances/{uuid}/subscribe", json=body, headers=headers
        )
        assert second.status_code == 400
        assert second.json()["message_key"] == "orchestrator.convertNotOnDemand"

    async def test_convert_keeps_locked_in_price_not_current_sku_price(self, client, sm, fake):
        """The conversion quotes from the price snapshot taken at instance creation."""
        headers, uuid, _user_id = await self._on_demand_running(
            client, sm, fake, "u13911100094@test.local"
        )
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
            snapshot, sku_id = inst.price_hourly, inst.sku_id
            await s.execute(
                update(Sku).where(Sku.id == sku_id).values(price_hourly=Decimal("99.0000"))
            )
            await s.commit()
        resp = await client.post(
            f"/api/v1/instances/{uuid}/subscribe",
            json={"period": "month", "period_count": 1},
            headers=headers,
        )
        assert resp.status_code == 200
        assert Decimal(resp.json()["quote"]["base_hourly"]) == snapshot

    async def test_stopped_instance_converts_without_extra_bill(self, client, sm, fake):
        """Converting a stopped instance adds no bill rows."""
        headers, uuid, _user_id = await self._on_demand_running(
            client, sm, fake, "u13911100095@test.local"
        )
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        async with sm() as s:
            instance_id = (
                (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one().id
            )
            before = len(
                (await s.execute(select(BillHourly).where(BillHourly.instance_id == instance_id)))
                .scalars()
                .all()
            )
        resp = await client.post(
            f"/api/v1/instances/{uuid}/subscribe",
            json={"period": "day", "period_count": 1},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        async with sm() as s:
            after = len(
                (await s.execute(select(BillHourly).where(BillHourly.instance_id == instance_id)))
                .scalars()
                .all()
            )
        assert after == before

    async def test_convert_refused_when_settlement_is_far_behind(self, client, sm, fake):
        """A conversion is refused while settlement lags badly."""
        from app.core.timeutil import hour_floor
        from app.modules.billing.models import SettlementWatermark

        headers, uuid, _user_id = await self._on_demand_running(
            client, sm, fake, "u13911100096@test.local"
        )
        async with sm() as s:
            s.add(
                SettlementWatermark(
                    key="hourly", settled_through=hour_floor(now_utc()) - timedelta(days=10)
                )
            )
            await s.commit()
        resp = await client.post(
            f"/api/v1/instances/{uuid}/subscribe",
            json={"period": "month", "period_count": 1},
            headers=headers,
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "billing.settlementBehind"


class TestUnstartedPrepay:
    async def test_schedule_timeout_refunds_prepay(self, client, sm, fake):
        from app.core.config import get_settings
        from app.modules.billing.models import BalanceLedger, Subscription
        from app.modules.notify.models import Notification

        headers, user_id, key_id = await funded_user(
            client, sm, "u13911100099@test.local", "5000.00"
        )
        sku_id = await create_test_sku(sm)
        await seed_node_spec(sm, node_name="node-timeout")
        code, data = await buy_subscription(client, headers, sku_id, key_id)
        assert code == 202, data
        await drain(sm)
        async with sm() as s:
            paid = (
                await s.execute(select(Subscription).where(Subscription.user_id == user_id))
            ).scalar_one()
            amount_paid = paid.amount_paid
            assert await wallet.get_balance(s, user_id) == Decimal("5000.00") - amount_paid
        settings = get_settings()
        saved = settings.creating_timeout_seconds
        settings.creating_timeout_seconds = 0
        try:
            await reconcile_once(sm)
        finally:
            settings.creating_timeout_seconds = saved
        async with sm() as s:
            inst = (
                await s.execute(select(Instance).where(Instance.uuid == data["uuid"]))
            ).scalar_one()
            assert inst.status == "failed"
            assert await wallet.get_balance(s, user_id) == Decimal("5000.00")
            sub = (
                await s.execute(select(Subscription).where(Subscription.user_id == user_id))
            ).scalar_one()
            assert sub.status == "cancelled"
            refund = (
                await s.execute(
                    select(BalanceLedger).where(
                        BalanceLedger.user_id == user_id, BalanceLedger.type == "refund"
                    )
                )
            ).scalar_one()
            assert refund.amount == amount_paid and refund.ref_type == "subscription"
            note = (
                await s.execute(
                    select(Notification).where(
                        Notification.user_id == user_id,
                        Notification.dedup_key == f"schedule_timeout:{inst.id}",
                    )
                )
            ).scalar_one()
            assert (
                "returned to your balance" in note.content
                and "nothing was charged" not in note.content
            )


def _gauge_value(gauge) -> float:
    return float(gauge.collect()[0].samples[0].value)


class TestDueCoverageRaces:
    async def _seed_due(self, sm, *, auto_renew: bool) -> tuple[int, int, str]:
        from app.modules.billing import subscriptions
        from tests.helpers import seed_instance

        instance_id, uuid = await seed_instance(
            sm, status="running", market="subscription", price="1.0000"
        )
        async with sm() as session:
            row, _ = await subscriptions.charge_new(
                session,
                user_id=1,
                instance_id=instance_id,
                instance_name="t",
                sku_id=1,
                base_hourly=Decimal("1.0000"),
                gpu_count=1,
                period="day",
                period_count=1,
                idempotency_key=None,
            )
            row.started_at = now_utc() - timedelta(days=1, minutes=1)
            row.expires_at = now_utc() - timedelta(minutes=1)
            row.auto_renew = auto_renew
            await session.commit()
            return instance_id, row.id, uuid

    @pytest.mark.parametrize("auto_renew", [False, True])
    async def test_manual_renewal_wins_before_initial_lock(self, sm, monkeypatch, auto_renew):
        """An identity-map snapshot of the old row cannot expire a newly purchased period."""
        from unittest.mock import AsyncMock

        from app.modules.billing import subscriptions
        from app.modules.orchestrator import queries, service

        instance_id, subscription_id, uuid = await self._seed_due(sm, auto_renew=auto_renew)
        original_lock = queries.lock_instance
        renewed = False

        async def renew_before_lock(session, iid):
            nonlocal renewed
            if not renewed:
                renewed = True
                async with sm() as other:
                    await service.renew_instance(
                        other, 1, uuid, period="day", period_count=1, idempotency_key=None
                    )
            return await original_lock(session, iid)

        monkeypatch.setattr(queries, "lock_instance", renew_before_lock)
        monkeypatch.setattr(queries, "lock_instance_for_billing", renew_before_lock)
        notice = AsyncMock()
        monkeypatch.setattr(subscriptions.notify_service, "send_subscription_notice", notice)
        counts = {"warned": 0, "renewed": 0, "renew_failed": 0, "stopped": 0, "frozen": 0}
        async with sm() as session:
            stale = await session.get(Subscription, subscription_id)
            assert stale is not None and stale.status == "active"
            await subscriptions._handle_due(session, subscription_id, counts)
            await session.commit()
        assert renewed and not any(counts.values())
        notice.assert_not_awaited()
        async with sm() as session:
            instance = await session.get(Instance, instance_id)
            assert instance is not None and instance.status == "running"
            latest = await subscriptions.current_for_instance(session, instance_id)
            assert latest is not None and latest.id != subscription_id
            assert latest.status == "active" and latest.expires_at > now_utc()
            assert latest.renewed_from_id == subscription_id
            old = await session.get(Subscription, subscription_id)
            assert old is not None and old.status == "expired"

    async def test_locked_coverage_refreshes_same_identity(self, sm):
        """FOR UPDATE must populate an existing ORM identity, not just take its database lock."""
        from app.modules.billing import subscriptions
        from app.modules.orchestrator import queries

        instance_id, subscription_id, _ = await self._seed_due(sm, auto_renew=False)
        async with sm() as session:
            stale = await session.get(Subscription, subscription_id)
            assert stale is not None and stale.expires_at < now_utc()
            future = now_utc() + timedelta(days=1)
            async with sm() as other:
                await queries.lock_instance(other, instance_id)
                await wallet.lock_wallet(other, 1)
                row = await subscriptions.current_for_instance(other, instance_id, for_update=True)
                assert row is not None
                row.expires_at = future
                await other.commit()
            assert (
                await subscriptions._lock_due_subscription(session, instance_id, subscription_id)
                is None
            )
            assert stale.expires_at == future

    @pytest.mark.parametrize("interleaving", ["renew", "stop"])
    async def test_auto_renew_failure_relocks_after_notice_commit(
        self, sm, monkeypatch, interleaving
    ):
        """The commit gap may contain manual renewal or a completed stop; use fresh locked state."""
        from unittest.mock import AsyncMock

        from sqlalchemy.exc import DBAPIError

        from app.modules.billing import subscriptions
        from app.modules.orchestrator import queries, service, transitions

        instance_id, subscription_id, uuid = await self._seed_due(sm, auto_renew=True)
        async with sm() as session:
            balance = await wallet.get_balance(session, 1)
            await wallet.debit(session, 1, balance, type_="adjust", allow_negative=False)
            await session.commit()
        notice = AsyncMock()
        monkeypatch.setattr(subscriptions.notify_service, "send_subscription_notice", notice)
        real_expire = subscriptions._expire_instance
        expiry_calls = 0

        async def expire_with_lock_check(session, instance, counts):
            nonlocal expiry_calls
            expiry_calls += 1
            async with sm() as contender:
                with pytest.raises(DBAPIError) as exc:
                    await contender.execute(
                        select(Instance.id)
                        .where(Instance.id == instance_id)
                        .with_for_update(nowait=True)
                    )
                assert getattr(exc.value.orig, "sqlstate", None) == "55P03"
            await real_expire(session, instance, counts)

        monkeypatch.setattr(subscriptions, "_expire_instance", expire_with_lock_check)
        counts = {"warned": 0, "renewed": 0, "renew_failed": 0, "stopped": 0, "frozen": 0}
        changed = False
        async with sm() as session:
            real_commit = session.commit

            async def commit_then_change():
                nonlocal changed
                await real_commit()
                if changed:
                    return
                changed = True
                if interleaving == "renew":
                    await fund_wallet(sm, 1, "100.00")
                    async with sm() as other:
                        await service.renew_instance(
                            other, 1, uuid, period="day", period_count=1, idempotency_key=None
                        )
                else:
                    async with sm() as other:
                        fresh = await queries.lock_instance(other, instance_id)
                        assert fresh is not None
                        await transitions.system_stop(other, fresh, reason="user_stop")
                        await transitions.transition(
                            other, fresh, "stopped", reason="stopped", actor="system"
                        )
                        await other.commit()

            monkeypatch.setattr(session, "commit", commit_then_change)
            await subscriptions._handle_due(session, subscription_id, counts)
            await session.commit()
        assert changed and counts["renew_failed"] == 1 and counts["stopped"] == 0
        async with sm() as session:
            instance = await session.get(Instance, instance_id)
            latest = await subscriptions.current_for_instance(session, instance_id)
            assert instance is not None and latest is not None
            if interleaving == "renew":
                assert instance.status == "running" and latest.status == "active"
                assert latest.id != subscription_id and expiry_calls == 0
                assert counts["frozen"] == 0
            else:
                assert instance.status == "frozen" and latest.status == "expired"
                assert latest.id == subscription_id and expiry_calls == 1
                assert counts["frozen"] == 1


class TestDueCoverageUnit:
    @pytest.mark.parametrize("still_due", [False, True])
    async def test_commit_requires_new_locked_coverage(self, monkeypatch, still_due):
        """No Docker: execute _handle_due and require a second lock/coverage result after commit."""
        from unittest.mock import AsyncMock, MagicMock

        from app.modules.billing import subscriptions

        row = Subscription(
            id=1,
            instance_id=1,
            user_id=1,
            status="active",
            auto_renew=True,
            expires_at=now_utc() - timedelta(minutes=1),
        )
        before = Instance(id=1, status="running", market="subscription")
        after = Instance(id=1, status="stopped", market="subscription")
        result = MagicMock()
        result.scalar_one_or_none.return_value = row
        session = AsyncMock()
        session.execute.return_value = result
        lock = AsyncMock(side_effect=[(before, row), (after, row) if still_due else None])
        monkeypatch.setattr(subscriptions, "_lock_due_subscription", lock)
        monkeypatch.setattr(subscriptions, "_try_auto_renew", AsyncMock(return_value=False))
        expire = AsyncMock()
        monkeypatch.setattr(subscriptions, "_expire_instance", expire)
        counts = {"warned": 0, "renewed": 0, "renew_failed": 0, "stopped": 0, "frozen": 0}
        await subscriptions._handle_due(session, 1, counts)
        assert lock.await_count == 2
        session.commit.assert_awaited_once()
        if still_due:
            expire.assert_awaited_once_with(session, after, counts)
            assert row.status == "expired"
        else:
            expire.assert_not_awaited()
            assert row.status == "active"


class TestExpiredSweep:
    async def test_expiry_during_starting_is_stopped_next_round(self, client, sm, fake):
        """Expiry inside the starting window: untouched this round, subscription → expired, metric
        1;
        once running the next round stops and then freezes it.
        A failure means "creating/starting slipping past the expiry patrol runs free forever" is
        back."""
        from app.core.metrics import SUBSCRIPTION_UNPAID_RUNNING

        headers, uuid, user_id, _, _ = await provision_subscription(
            client, sm, fake, "u13911100201@test.local", period="day"
        )
        ns = f"tenant-{user_id}"
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "stopped"
        fake.auto_ready = False
        resp = await client.post(f"/api/v1/instances/{uuid}/start", headers=headers)
        assert resp.status_code == 200, resp.text
        await drain(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "starting"
        async with sm() as s:
            await s.execute(
                update(Subscription)
                .where(Subscription.user_id == user_id)
                .values(expires_at=now_utc() - timedelta(minutes=1))
            )
            await s.commit()

        counts = await subscription_patrol(sm)
        assert counts["stopped"] == 0 and counts["frozen"] == 0
        assert (await get_instance(client, headers, uuid))["status"] == "starting"
        async with sm() as s:
            sub = (
                await s.execute(select(Subscription).where(Subscription.user_id == user_id))
            ).scalar_one()
            assert sub.status == "expired"
        assert _gauge_value(SUBSCRIPTION_UNPAID_RUNNING) == 1

        fake.mark_ready(ns, uuid)
        await reconcile_once(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "running"
        counts = await subscription_patrol(sm)
        assert counts["stopped"] == 1
        async with sm() as s:
            inst = (await s.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
            assert inst.status == "stopping"
        await drain(sm)
        await reconcile_once(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "stopped"
        counts = await subscription_patrol(sm)
        assert counts["frozen"] == 1
        assert (await get_instance(client, headers, uuid))["status"] == "frozen"
        assert _gauge_value(SUBSCRIPTION_UNPAID_RUNNING) == 0

    async def test_renewed_instance_is_not_swept(self, client, sm, fake):
        """A running instance renewed after expiry is covered and not stopped by the expiry
        sweep."""
        headers, uuid, _user_id, _, _ = await provision_subscription(
            client, sm, fake, "u13911100202@test.local"
        )
        counts = await subscription_patrol(sm)
        assert counts["stopped"] == 0
        assert (await get_instance(client, headers, uuid))["status"] == "running"
