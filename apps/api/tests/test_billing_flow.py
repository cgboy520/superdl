"""计费与编排的集成:尾账(计费边同事务)与欠费链路(预警→停机→冻结→回收→解冻)。"""

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
        """无钱包行的用户被并发入账:只留一行且每笔入账都落账。"""
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
        assert w.balance == Decimal("100.00") - bill.amount  # 尾账与迁移同事务入账

        # 账单接口可见
        bills = (await client.get("/api/v1/bills/hourly", headers=headers)).json()
        assert len(bills["items"]) == 1

    async def test_pod_lost_also_charges_tail(self, client, sm, fake):
        """running→failed 同样是计费边,尾账照出。"""
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
        # 清空余额
        async with sm() as session:
            balance = await wallet.get_balance(session, user_id)
            await wallet.debit(
                session, user_id, balance, type_="adjust", remark="test-drain", allow_negative=True
            )
            await session.commit()

        # 巡检 → 自动停机
        counts = await balance_patrol(sm)
        assert counts["stopped"] == 1
        assert (await get_instance(client, headers, uuid))["status"] == "stopping"
        await drain(sm)
        await reconcile_once(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "stopped"

        # 巡检 → 冻结(72h 倒计时)
        counts = await balance_patrol(sm)
        assert counts["frozen"] == 1
        data = await get_instance(client, headers, uuid)
        assert data["status"] == "frozen"
        assert data["frozen_deadline"] is not None

        # 到期 → 回收
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

        # 事件链完整可追溯
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
        await balance_patrol(sm)  # 停机
        await drain(sm)
        await reconcile_once(sm)
        await balance_patrol(sm)  # 冻结
        assert (await get_instance(client, headers, uuid))["status"] == "frozen"

        # 充值 → 解冻回 stopped
        async with sm() as session:
            await wallet.credit(session, user_id, Decimal("50.00"), type_="recharge")
            await session.commit()
        counts = await balance_patrol(sm)
        assert counts["unfrozen"] == 1
        data = await get_instance(client, headers, uuid)
        assert data["status"] == "stopped"
        assert data["frozen_deadline"] is None

    async def test_arrears_stop_rereads_balance_in_lock(self, client, sm, fake, monkeypatch):
        """停机判定前锁内二次读余额:窗口内刚充值 → 不停机。"""
        from app.modules.billing import patrol as patrol_mod

        headers, uuid, user_id = await provision_running(client, sm, fake)
        async with sm() as session:
            balance = await wallet.get_balance(session, user_id)
            await wallet.debit(
                session, user_id, balance, type_="adjust", remark="drain", allow_negative=True
            )
            await session.commit()

        # 无锁粗筛看到旧值 0;随后充值落库,锁内读到真值
        async def stale_get_balance(session, uid):
            return Decimal("0.00")

        monkeypatch.setattr(patrol_mod.wallet, "get_available_balance", stale_get_balance)
        async with sm() as session:
            await wallet.credit(session, user_id, Decimal("50.00"), type_="recharge")
            await session.commit()
        counts = await balance_patrol(sm)
        assert counts["stopped"] == 0  # 锁内读到 50,不停机
        assert (await get_instance(client, headers, uuid))["status"] == "running"
        # 对照:锁内真值仍为负时照常停机
        async with sm() as session:
            await wallet.debit(
                session, user_id, Decimal("50.00"), type_="adjust", allow_negative=True
            )
            await session.commit()
        counts = await balance_patrol(sm)
        assert counts["stopped"] == 1


class TestBillingApiEdges:
    async def test_ledger_cursor_pagination(self, client, sm):
        """游标续页全程走查:limit 截断 → 拿 next_cursor 续 → 两页无重叠。"""
        data = await register(client, "13900000701")
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
        assert not ids1 & ids2  # 无重叠

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
        # 月度汇总按实例归因并补实例名
        summary = (
            await client.get("/api/v1/bills/summary", params={"month": month}, headers=headers)
        ).json()
        assert [(i["instance_id"], i["instance_name"]) for i in summary["items"]] == [
            (inst["id"], inst["name"])
        ]
        assert summary["gpu_total"] == bills["items"][0]["amount"]
