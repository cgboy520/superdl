"""管理端运营 API:租户/调账双复核/节点与超卖报表/审计检索/死信重放/收入报表/公告。"""

import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError, ErrorCode
from app.core.k8s import set_orchestrator
from app.core.k8s.fake import FakeOrchestrator
from app.core.outbox import OutboxTask, drain
from app.core.timeutil import now_utc
from app.modules.adminapi import service as admin_service
from app.modules.adminapi.service import create_admin
from app.modules.billing.models import BalanceLedger
from app.modules.notify.models import Notification
from app.modules.orchestrator.reconciler import reconcile_once
from tests.test_account_auth import register
from tests.test_catalog import admin_headers
from tests.test_orchestrator_lifecycle import _provision_running

pytestmark = pytest.mark.usefixtures("fake")


@pytest.fixture
def fake():
    orch = FakeOrchestrator(auto_ready=False)
    set_orchestrator(orch)
    yield orch
    set_orchestrator(None)


async def second_admin_headers(
    sm: async_sessionmaker[AsyncSession], client, username: str, role: str = "finance"
) -> dict[str, str]:
    async with sm() as session:
        await create_admin(session, username, "pass1234", role)
    resp = await client.post(
        "/api/admin/v1/auth/login", json={"username": username, "password": "pass1234"}
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _make_dead_task(sm) -> int:
    async with sm() as session:
        task = OutboxTask(
            type="instance.create",
            payload={"instance_id": 999},
            status="dead",
            retries=5,
            last_error="pod schedule timeout",
        )
        session.add(task)
        await session.commit()
        return task.id


class TestTenants:
    async def test_list_and_freeze(self, client, sm, fake):
        headers, _uuid, user_id = await _provision_running(client, sm, fake)
        ah = await admin_headers(sm, client, role="ops")

        tenants = (await client.get("/api/admin/v1/tenants", headers=ah)).json()
        me = next(t for t in tenants if t["id"] == user_id)
        assert me["phone_masked"].startswith("139") and "****" in me["phone_masked"]
        assert me["instances"] == 1
        assert me["balance"] == "100.00"

        resp = await client.post(
            f"/api/admin/v1/tenants/{user_id}/freeze", json={"reason": "违规"}, headers=ah
        )
        assert resp.json()["status"] == "frozen"
        # 冻结后用户请求被拒
        resp = await client.get("/api/v1/me", headers=headers)
        assert resp.status_code == 403

        resp = await client.post(
            f"/api/admin/v1/tenants/{user_id}/unfreeze", json={"reason": "误判"}, headers=ah
        )
        assert resp.json()["status"] == "active"


class TestAdjustments:
    async def test_dual_review_flow(self, client, sm, fake):
        headers, _uuid, user_id = await _provision_running(client, sm, fake)
        finance_a = await second_admin_headers(sm, client, "fin-a")
        finance_b = await second_admin_headers(sm, client, "fin-b")

        resp = await client.post(
            "/api/admin/v1/adjustments",
            json={"user_id": user_id, "amount": "25.50", "reason": "GPU 故障补偿"},
            headers=finance_a,
        )
        assert resp.status_code == 201, resp.text
        adj_id = resp.json()["id"]

        # 发起人自审 → 403
        resp = await client.post(
            f"/api/admin/v1/adjustments/{adj_id}/review",
            json={"approve": True},
            headers=finance_a,
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ADMIN_SECOND_REVIEW_REQUIRED"

        # 第二管理员复核 → 生效
        resp = await client.post(
            f"/api/admin/v1/adjustments/{adj_id}/review",
            json={"approve": True, "comment": "属实"},
            headers=finance_b,
        )
        assert resp.json()["status"] == "approved"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "125.50"

        # 已处理的单不可重复复核
        resp = await client.post(
            f"/api/admin/v1/adjustments/{adj_id}/review",
            json={"approve": True},
            headers=finance_b,
        )
        assert resp.status_code == 409

    async def test_negative_adjustment_and_reject(self, client, sm, fake):
        headers, _uuid, user_id = await _provision_running(client, sm, fake)
        fin_a = await second_admin_headers(sm, client, "fin-c")
        fin_b = await second_admin_headers(sm, client, "fin-d")

        resp = await client.post(
            "/api/admin/v1/adjustments",
            json={"user_id": user_id, "amount": "-10.00", "reason": "误退回收"},
            headers=fin_a,
        )
        adj_id = resp.json()["id"]
        resp = await client.post(
            f"/api/admin/v1/adjustments/{adj_id}/review",
            json={"approve": False, "comment": "证据不足"},
            headers=fin_b,
        )
        assert resp.json()["status"] == "rejected"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "100.00"  # 驳回不动账

    async def test_readonly_cannot_create(self, client, sm, fake):
        ro = await admin_headers(sm, client, role="readonly")
        resp = await client.post(
            "/api/admin/v1/adjustments",
            json={"user_id": 1, "amount": "1.00", "reason": "test"},
            headers=ro,
        )
        assert resp.status_code == 403

    async def test_concurrent_review_single_credit(self, client, sm, fake):
        """P0 竞态回归:两名复核人并发 approve 同一单,行锁保证只入账一次。"""
        headers, _uuid, user_id = await _provision_running(client, sm, fake)
        async with sm() as session:
            creator = await create_admin(session, "fin-race-a", "pass1234", "finance")
            r1 = await create_admin(session, "fin-race-b", "pass1234", "finance")
            r2 = await create_admin(session, "fin-race-c", "pass1234", "finance")
            creator_id, r1_id, r2_id = creator.id, r1.id, r2.id
        async with sm() as session:
            adj = await admin_service.create_adjustment(
                session,
                user_id=user_id,
                amount="10.00",
                reason="并发复核竞态",
                created_by=creator_id,
            )
            adj_id = adj.id

        async def review(reviewer_id: int) -> str:
            async with sm() as session:
                try:
                    await admin_service.review_adjustment(
                        session, adj_id, approve=True, reviewer_id=reviewer_id, comment=None
                    )
                    return "approved"
                except AppError as exc:
                    return str(exc.code)

        results = await asyncio.gather(review(r1_id), review(r2_id))
        assert sorted(results) == [str(ErrorCode.CONFLICT), "approved"]
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "110.00"  # 100 + 10,并发只入账一次
        async with sm() as session:
            entries = (
                (await session.execute(select(BalanceLedger).where(BalanceLedger.type == "adjust")))
                .scalars()
                .all()
            )
        assert len(entries) == 1


class TestNodesAndReports:
    async def test_nodes_view(self, client, sm, fake):
        from app.modules.nodes.patrol import node_spec_patrol

        await node_spec_patrol(sm)  # 节点视图只认巡检台账
        ah = await admin_headers(sm, client, role="ops")
        nodes = (await client.get("/api/admin/v1/nodes", headers=ah)).json()
        pools = {n["pool_label"] for n in nodes}
        assert {"kata", "hami", "mig"} <= pools

    async def test_oversell_report(self, client, sm, fake):
        _headers, _uuid, _user_id = await _provision_running(client, sm, fake)  # hami 池 50% × 1
        ah = await admin_headers(sm, client, role="finance")
        report = (await client.get("/api/admin/v1/reports/oversell", headers=ah)).json()
        hami = next(r for r in report if r["pool"] == "hami")
        assert hami["physical_gpus"] == 32
        assert hami["sold_share"] == 0.5
        assert hami["oversell_ratio"] == round(0.5 / 32, 3)

    async def test_oversell_report_pool_scoped_utilization(self, client, sm, fake):
        """P0 回归:利用率按池加权聚合;无数据的池必须是 null,不得用集群均值冒充。"""
        from app.modules.metering.models import UsageHourly
        from app.modules.orchestrator.models import Instance

        _headers, _uuid, _user_id = await _provision_running(client, sm, fake)  # hami 池实例
        hour = now_utc().replace(minute=0, second=0, microsecond=0)
        async with sm() as session:
            inst_id = (await session.execute(select(Instance.id))).scalar_one()
            session.add_all(
                [
                    UsageHourly(
                        instance_id=inst_id,
                        hour_start=hour - timedelta(hours=2),
                        gpu_util_avg=30.0,
                    ),
                    UsageHourly(
                        instance_id=inst_id,
                        hour_start=hour - timedelta(hours=1),
                        gpu_util_avg=60.0,
                    ),
                ]
            )
            await session.commit()

        ah = await admin_headers(sm, client, role="finance")
        report = (await client.get("/api/admin/v1/reports/oversell", headers=ah)).json()
        by_pool = {r["pool"]: r for r in report}
        assert by_pool["hami"]["util_avg_24h"] == 45.0  # (30+60)/2,仅 hami 池
        assert by_pool["kata"]["util_avg_24h"] is None
        assert by_pool["mig"]["util_avg_24h"] is None

    async def test_audit_search(self, client, sm, fake):
        _headers, _uuid, _user_id = await _provision_running(client, sm, fake)
        ah = await admin_headers(sm, client, role="admin")
        rows = (await client.get("/api/admin/v1/audit", headers=ah)).json()
        assert any(r["action"] == "POST /api/v1/instances" for r in rows)
        user_rows = (
            await client.get("/api/admin/v1/audit", params={"actor_type": "user"}, headers=ah)
        ).json()
        assert all(r["actor_type"] == "user" for r in user_rows)


class TestOutboxDead:
    async def test_list_retry_and_discard(self, client: AsyncClient, sm):
        task_id = await _make_dead_task(sm)
        ah = await admin_headers(sm, client, role="ops")

        rows = (await client.get("/api/admin/v1/outbox/dead", headers=ah)).json()
        assert any(r["id"] == task_id for r in rows)

        # 重放:置回 pending,计数清零
        resp = await client.post(f"/api/admin/v1/outbox/{task_id}/retry", headers=ah)
        assert resp.status_code == 200
        async with sm() as session:
            task = await session.get(OutboxTask, task_id)
            assert task is not None and task.status == "pending" and task.retries == 0

        # 非 dead 状态不可重放/忽略
        resp = await client.post(f"/api/admin/v1/outbox/{task_id}/retry", headers=ah)
        assert resp.status_code == 400

        # 忽略需原因
        task2 = await _make_dead_task(sm)
        resp = await client.post(
            f"/api/admin/v1/outbox/{task2}/discard", json={"reason": "实例已人工清理"}, headers=ah
        )
        assert resp.status_code == 200
        async with sm() as session:
            task = await session.get(OutboxTask, task2)
            assert task is not None and task.status == "discarded"

    async def test_readonly_cannot_retry(self, client: AsyncClient, sm):
        task_id = await _make_dead_task(sm)
        ah = await admin_headers(sm, client, role="readonly")
        resp = await client.post(f"/api/admin/v1/outbox/{task_id}/retry", headers=ah)
        assert resp.status_code == 403


class TestRevenueReport:
    async def test_today_revenue_and_signups(self, client: AsyncClient, sm):
        data = await register(client, "13600000043")
        async with sm() as session:
            session.add(
                BalanceLedger(
                    user_id=data["user"]["id"],
                    type="consume",
                    amount=Decimal("-12.50"),
                    balance_after=Decimal("87.50"),
                )
            )
            await session.commit()

        ah = await admin_headers(sm, client, role="finance")
        resp = await client.get("/api/admin/v1/reports/revenue", headers=ah)
        assert resp.status_code == 200
        body = resp.json()
        assert body["today_revenue"] == "12.50"
        assert body["month_revenue"] == "12.50"
        assert body["today_signups"] >= 1


class TestAnnouncement:
    async def test_publish_reaches_all_active_users(self, client: AsyncClient, sm):
        u1 = await register(client, "13600000041")
        u2 = await register(client, "13600000042")
        ah = await admin_headers(sm, client, role="ops")

        resp = await client.post(
            "/api/admin/v1/announcements",
            json={
                "title": "8 月 24 日存储维护",
                "content": "维护期间数据盘可能抖动,实例不受影响。",
            },
            headers=ah,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["reached"] == 2

        async with sm() as session:
            rows = (
                (
                    await session.execute(
                        select(Notification).where(Notification.type == "announcement")
                    )
                )
                .scalars()
                .all()
            )
            assert {r.user_id for r in rows} == {u1["user"]["id"], u2["user"]["id"]}

        # 用户端可见
        headers = {"Authorization": f"Bearer {u1['access_token']}"}
        notifications = (await client.get("/api/v1/notifications", headers=headers)).json()
        assert any(n["type"] == "announcement" for n in notifications)


class TestTenantBillingDrilldown:
    """账单争议处理:管理端必须能看到任一租户的账单明细与资金流水。"""

    async def test_ledger_and_bills_pages(self, client, sm):
        from decimal import Decimal

        from app.modules.billing import wallet

        headers = await admin_headers(sm, client)
        async with sm() as session:
            await wallet.credit(session, 4242, Decimal("100.00"), type_="recharge", remark="充值")
            for i in range(3):
                await wallet.debit(
                    session,
                    4242,
                    Decimal("1.00"),
                    type_="consume",
                    ref_type="bill_hourly",
                    ref_id=str(i),
                )
            await session.commit()

        resp = await client.get("/api/admin/v1/tenants/4242/ledger?limit=2", headers=headers)
        assert resp.status_code == 200, resp.text
        page = resp.json()
        assert len(page["items"]) == 2
        assert page["next_cursor"]
        resp2 = await client.get(
            f"/api/admin/v1/tenants/4242/ledger?limit=2&cursor={page['next_cursor']}",
            headers=headers,
        )
        assert len(resp2.json()["items"]) == 2  # 翻页拿到剩余两条

        # 小时账单端点可达(该租户暂无账单 → 空页,不是 500)
        resp3 = await client.get("/api/admin/v1/tenants/4242/bills", headers=headers)
        assert resp3.status_code == 200
        assert resp3.json()["items"] == []


class TestFreezeStopsInstances:
    async def test_freeze_stops_running_instances(self, client, sm, fake):
        """封禁必须同时停机、停计费。

        此前 admin_set_user_status 只改 status + 撤 token:被封账号的 GPU 继续满载跑(封
        挖矿号时处置完全失效),而计费主链路不看用户状态 —— 被封用户继续每小时被扣,扣到 0
        之后走欠费链路把数据盘推向回收倒计时,他却连充值下单都做不了(deps 的 403 覆盖整个
        CurrentUser),既阻止不了扣费也无法自救。
        """
        h = await admin_headers(sm, client)
        _user_headers, uuid, user_id = await _provision_running(client, sm, fake, "13600000090")

        resp = await client.post(
            f"/api/admin/v1/tenants/{user_id}/freeze",
            json={"reason": "疑似挖矿"},
            headers=h,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "frozen"

        async with sm() as session:
            tasks = (
                (
                    await session.execute(
                        select(OutboxTask).where(OutboxTask.type == "instance.stop")
                    )
                )
                .scalars()
                .all()
            )
        assert len(tasks) == 1

        # 用户端此刻已经登不上,用管理端列表核对状态
        listed = (await client.get("/api/admin/v1/instances", headers=h)).json()
        assert [i["status"] for i in listed if i["uuid"] == uuid] == ["stopping"]
        await drain(sm)
        await reconcile_once(sm)
        listed = (await client.get("/api/admin/v1/instances", headers=h)).json()
        # 计费边(running→stopping→stopped)已闭合,后续小时不再产生账单
        assert [i["status"] for i in listed if i["uuid"] == uuid] == ["stopped"]

    async def test_unfreeze_does_not_auto_start(self, client, sm, fake):
        """解封不自动开机:解封瞬间批量拉起,余额不足的话立刻又欠费停机。"""
        h = await admin_headers(sm, client)
        _uh, uuid, user_id = await _provision_running(client, sm, fake, "13600000091")
        await client.post(
            f"/api/admin/v1/tenants/{user_id}/freeze", json={"reason": "核查"}, headers=h
        )
        await drain(sm)
        await reconcile_once(sm)
        resp = await client.post(
            f"/api/admin/v1/tenants/{user_id}/unfreeze", json={"reason": "核查完毕"}, headers=h
        )
        assert resp.status_code == 200
        listed = (await client.get("/api/admin/v1/instances", headers=h)).json()
        assert [i["status"] for i in listed if i["uuid"] == uuid] == ["stopped"]
