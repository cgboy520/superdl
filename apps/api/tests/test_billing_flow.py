"""Billing and orchestration integration: tail bill (same transaction as the billing edge) and the
arrears chain (warn → stop → freeze → reclaim → unfreeze)."""

import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.timeutil import now_utc
from app.modules.billing import wallet
from app.modules.billing.models import BillHourly, Wallet
from app.modules.billing.patrol import balance_patrol
from app.modules.orchestrator.models import Instance
from app.modules.orchestrator.reconciler import reconcile_once
from tests.helpers import backdate_running_event, drain, get_instance, provision_running, register

pytestmark = pytest.mark.usefixtures("fake")


class TestWalletFirstCreate:
    async def test_concurrent_first_credit_creates_single_row(self, sm):
        """Concurrent credits for a user without a wallet row: one row and every credit lands."""
        gate = asyncio.Barrier(5)

        async def credit_once() -> None:
            await gate.wait()
            async with sm() as s:
                await wallet.credit(s, 424242, Decimal("10.00"), type_="recharge")
                await s.commit()

        await asyncio.gather(*(credit_once() for _ in range(4)), gate.wait())
        async with sm() as s:
            rows = (await s.execute(select(Wallet).where(Wallet.user_id == 424242))).scalars().all()
        assert len(rows) == 1
        assert rows[0].balance == Decimal("40.00")


class TestTailBilling:
    async def test_stop_charges_tail_in_same_transaction(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession], fake
    ):
        headers, uuid, _user_id = await provision_running(client, sm, fake)
        expected = await backdate_running_event(sm, uuid, 30)

        resp = await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        assert resp.status_code == 200

        async with sm() as session:
            bill = (await session.execute(select(BillHourly))).scalar_one()
            w = (await session.execute(select(Wallet))).scalar_one()
        assert expected - 2 <= bill.seconds_used <= expected + 15
        assert bill.detail is not None and bill.detail["source"] == "tail"
        assert w.balance == Decimal("100.00") - bill.amount

        bills = (await client.get("/api/v1/bills/hourly", headers=headers)).json()
        assert len(bills["items"]) == 1

    async def test_pod_lost_also_charges_tail(self, client, sm, fake):
        """running→failed is a billing edge too, the tail bill is posted."""
        _headers, uuid, user_id = await provision_running(client, sm, fake)
        expected = await backdate_running_event(sm, uuid, 15)
        fake.kill_pod(f"tenant-{user_id}", uuid)
        await reconcile_once(sm)
        async with sm() as session:
            bill = (await session.execute(select(BillHourly))).scalar_one()
        assert expected - 2 <= bill.seconds_used <= expected + 15


class TestArrearsChain:
    async def test_zero_balance_stops_then_freezes_then_reclaims(self, client, sm, fake):
        headers, uuid, user_id = await provision_running(client, sm, fake)
        async with sm() as session:
            balance = await wallet.get_balance(session, user_id)
            await wallet.debit(
                session, user_id, balance, type_="adjust", remark="test-drain", allow_negative=True
            )
            await session.commit()

        counts = await balance_patrol(sm)
        assert counts["stopped"] == 1
        assert (await get_instance(client, headers, uuid))["status"] == "stopping"
        await drain(sm)
        await reconcile_once(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "stopped"

        counts = await balance_patrol(sm)
        assert counts["frozen"] == 1
        data = await get_instance(client, headers, uuid)
        assert data["status"] == "frozen"
        assert data["frozen_deadline"] is not None

        async with sm() as session:
            await session.execute(
                update(Instance)
                .where(Instance.uuid == uuid)
                .values(frozen_deadline=now_utc() - timedelta(hours=1))
            )
            await session.commit()
        counts = await balance_patrol(sm)
        assert counts["reclaimed"] == 1
        assert (await get_instance(client, headers, uuid))["status"] == "releasing"
        await drain(sm)
        await reconcile_once(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "released"

        events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=headers)).json()[
            "items"
        ]
        reasons = [e["reason"] for e in events]
        assert "arrears_stop" in reasons
        assert "arrears_freeze" in reasons
        assert "arrears_reclaim" in reasons

    async def test_recharge_unfreezes(self, client, sm, fake):
        headers, uuid, user_id = await provision_running(client, sm, fake)
        async with sm() as session:
            balance = await wallet.get_balance(session, user_id)
            await wallet.debit(
                session, user_id, balance, type_="adjust", remark="drain", allow_negative=True
            )
            await session.commit()
        await balance_patrol(sm)
        await drain(sm)
        await reconcile_once(sm)
        await balance_patrol(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "frozen"

        async with sm() as session:
            await wallet.credit(session, user_id, Decimal("50.00"), type_="recharge")
            await session.commit()
        counts = await balance_patrol(sm)
        assert counts["unfrozen"] == 1
        data = await get_instance(client, headers, uuid)
        assert data["status"] == "stopped"
        assert data["frozen_deadline"] is None

    async def test_arrears_stop_rereads_balance_in_lock(self, client, sm, fake, monkeypatch):
        """Second balance read under the lock before the stop decision: a top-up inside the window →
        no stop."""
        from app.modules.billing import patrol as patrol_mod

        headers, uuid, user_id = await provision_running(client, sm, fake)
        async with sm() as session:
            balance = await wallet.get_balance(session, user_id)
            await wallet.debit(
                session, user_id, balance, type_="adjust", remark="drain", allow_negative=True
            )
            await session.commit()

        async def stale_get_balance(session, uid):
            return Decimal("0.00")

        monkeypatch.setattr(patrol_mod.wallet, "get_available_balance", stale_get_balance)
        async with sm() as session:
            await wallet.credit(session, user_id, Decimal("50.00"), type_="recharge")
            await session.commit()
        counts = await balance_patrol(sm)
        assert counts["stopped"] == 0
        assert (await get_instance(client, headers, uuid))["status"] == "running"
        async with sm() as session:
            await wallet.debit(
                session, user_id, Decimal("50.00"), type_="adjust", allow_negative=True
            )
            await session.commit()
        counts = await balance_patrol(sm)
        assert counts["stopped"] == 1


class TestBillingApiEdges:
    async def test_ledger_cursor_pagination(self, client, sm):
        """Cursor continuation end to end: limit truncates → continue with next_cursor → no overlap
        between pages."""
        data = await register(client, "u13900000701@test.local")
        headers, user_id = {"Authorization": f"Bearer {data['access_token']}"}, data["user"]["id"]
        async with sm() as session:
            for i in range(5):
                await wallet.credit(
                    session, user_id, Decimal("1.00"), type_="recharge", remark=f"r{i}"
                )
            await session.commit()
        page1 = (
            await client.get("/api/v1/wallet/ledger", params={"limit": 3}, headers=headers)
        ).json()
        assert len(page1["items"]) == 3
        assert page1["next_cursor"]
        page2 = (
            await client.get(
                "/api/v1/wallet/ledger",
                params={"limit": 10, "cursor": page1["next_cursor"]},
                headers=headers,
            )
        ).json()
        ids1 = {e["id"] for e in page1["items"]}
        ids2 = {e["id"] for e in page2["items"]}
        assert not ids1 & ids2

    async def test_bills_filters(self, client, sm, fake):
        headers, uuid, _user_id = await provision_running(client, sm, fake)
        await backdate_running_event(sm, uuid, 20)
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        month = now_utc().strftime("%Y-%m")
        inst = (await client.get(f"/api/v1/instances/{uuid}", headers=headers)).json()

        bills = (
            await client.get(
                "/api/v1/bills/hourly",
                params={"instance_id": inst["id"], "month": month},
                headers=headers,
            )
        ).json()
        assert len(bills["items"]) == 1
        summary = (
            await client.get("/api/v1/bills/summary", params={"month": month}, headers=headers)
        ).json()
        assert [(i["instance_id"], i["instance_name"]) for i in summary["items"]] == [
            (inst["id"], inst["name"])
        ]
        assert summary["gpu_total"] == bills["items"][0]["amount"]
