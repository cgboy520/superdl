"""计费与编排的集成:尾账(计费边同事务)与欠费链路(预警→停机→冻结→回收→解冻)。"""

from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.k8s import set_orchestrator
from app.core.k8s.fake import FakeOrchestrator
from app.core.outbox import drain
from app.core.timeutil import now_utc
from app.modules.billing import wallet
from app.modules.billing.models import BillHourly, Wallet
from app.modules.billing.patrol import balance_patrol
from app.modules.orchestrator.models import Instance, InstanceEvent
from app.modules.orchestrator.reconciler import reconcile_once
from tests.test_orchestrator_lifecycle import _provision_running, get_instance

pytestmark = pytest.mark.usefixtures("fake")


@pytest.fixture
def fake():
    orch = FakeOrchestrator(auto_ready=False)
    set_orchestrator(orch)
    yield orch
    set_orchestrator(None)


async def backdate_running_event(
    sm: async_sessionmaker[AsyncSession], uuid: str, minutes: int
) -> int:
    """把进入 running 的事件回拨(钳制在当前自然小时内,尾账只覆盖当前小时)。

    返回预期已运行秒数(近似,断言时留余量)。
    """
    from app.core.timeutil import hour_floor

    now = now_utc()
    start = max(hour_floor(now), now - timedelta(minutes=minutes))
    async with sm() as session:
        inst = (await session.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
        await session.execute(
            update(InstanceEvent)
            .where(InstanceEvent.instance_id == inst.id, InstanceEvent.to_status == "running")
            .values(created_at=start)
        )
        await session.commit()
    return int((now - start).total_seconds())


class TestTailBilling:
    async def test_stop_charges_tail_in_same_transaction(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession], fake
    ):
        headers, uuid, user_id = await _provision_running(client, sm, fake)
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
        """故障停费:running→failed 同样是计费边,尾账照出。"""
        headers, uuid, user_id = await _provision_running(client, sm, fake)
        expected = await backdate_running_event(sm, uuid, 15)
        fake.kill_pod(f"tenant-{user_id}", uuid)
        await reconcile_once(sm)
        async with sm() as session:
            bill = (await session.execute(select(BillHourly))).scalar_one()
        assert expected - 2 <= bill.seconds_used <= expected + 15


class TestArrearsChain:
    async def test_zero_balance_stops_then_freezes_then_reclaims(self, client, sm, fake):
        headers, uuid, user_id = await _provision_running(client, sm, fake)
        # 清空余额
        async with sm() as session:
            balance = await wallet.get_balance(session, user_id)
            await wallet.debit(session, user_id, balance, type_="adjust", remark="test-drain")
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
        events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=headers)).json()
        reasons = [e["reason"] for e in events]
        assert "arrears_stop" in reasons
        assert "arrears_freeze" in reasons
        assert "arrears_reclaim" in reasons

    async def test_recharge_unfreezes(self, client, sm, fake):
        headers, uuid, user_id = await _provision_running(client, sm, fake)
        async with sm() as session:
            balance = await wallet.get_balance(session, user_id)
            await wallet.debit(session, user_id, balance, type_="adjust", remark="drain")
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

    async def test_low_balance_warning(self, client, sm, fake):
        headers, uuid, user_id = await _provision_running(client, sm, fake)
        # 余额压到不足 24h(单价 1.68/时 → 24h 需 40.32;留 10)
        async with sm() as session:
            balance = await wallet.get_balance(session, user_id)
            await wallet.debit(
                session, user_id, balance - Decimal("10.00"), type_="adjust", remark="t"
            )
            await session.commit()
        counts = await balance_patrol(sm)
        assert counts["warned"] == 1
        # 实例不受影响
        assert (await get_instance(client, headers, uuid))["status"] == "running"


class TestWalletApi:
    async def test_wallet_and_ledger_endpoints(self, client, sm, fake):
        headers, uuid, user_id = await _provision_running(client, sm, fake)
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "100.00"
        ledger = (await client.get("/api/v1/wallet/ledger", headers=headers)).json()
        assert ledger["items"][0]["type"] == "recharge"

    async def test_summary_endpoint(self, client, sm, fake):
        headers, uuid, _user_id = await _provision_running(client, sm, fake)
        expected = await backdate_running_event(sm, uuid, 30)
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        month = now_utc().strftime("%Y-%m")
        summary = (
            await client.get("/api/v1/bills/summary", params={"month": month}, headers=headers)
        ).json()
        assert summary["items"][0]["total_seconds"] >= max(1, expected - 2)


class TestBillingApiEdges:
    async def test_ledger_cursor_pagination(self, client, sm, fake):
        headers, uuid, user_id = await _provision_running(client, sm, fake)
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

    async def test_bills_filters_and_invalid_month(self, client, sm, fake):
        headers, uuid, _user_id = await _provision_running(client, sm, fake)
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

        resp = await client.get(
            "/api/v1/bills/hourly", params={"month": "2026/08"}, headers=headers
        )
        assert resp.json()["code"] == "VALIDATION_ERROR"

        resp = await client.get("/api/v1/bills/summary", params={"month": "bad"}, headers=headers)
        assert resp.status_code == 400

    async def test_invalid_cursor_rejected(self, client, sm, fake):
        headers, _uuid, _user_id = await _provision_running(client, sm, fake)
        resp = await client.get("/api/v1/wallet/ledger", params={"cursor": "%%%"}, headers=headers)
        assert resp.json()["code"] == "VALIDATION_ERROR"

    async def test_december_month_parse(self, client, sm, fake):
        headers, _uuid, _user_id = await _provision_running(client, sm, fake)
        resp = await client.get(
            "/api/v1/bills/summary", params={"month": "2026-12"}, headers=headers
        )
        assert resp.status_code == 200
