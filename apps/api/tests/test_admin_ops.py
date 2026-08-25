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
from app.core.outbox import OutboxTask
from app.core.timeutil import now_utc
from app.modules.adminapi import service as admin_service
from app.modules.adminapi.service import create_admin
from app.modules.billing.models import BalanceLedger
from app.modules.notify.models import Notification
from app.modules.orchestrator.reconciler import reconcile_once
from tests.helpers import create_user_with_key, drain
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
    """指定用户名建管理员并登录(默认 finance:调账双人复核需要第二位财务)。"""
    return await admin_headers(sm, client, role, username=username)


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

        tenants = (await client.get("/api/admin/v1/tenants", headers=ah)).json()["items"]
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

    async def test_idempotency_scope_and_fingerprint(self, client, sm, fake):
        """幂等键加固(P2):同键同体重放 → replay;同键异体 → 409 指纹不符;
        同键同体跨租户 → 各开各的单(作用域含 user_id,弱键跨租户不再误判重放)。"""
        _h1, _u1, user1 = await _provision_running(client, sm, fake)
        _h2, u2id, _k2 = await create_user_with_key(client, "13900000141")
        finance = await second_admin_headers(sm, client, "fin-idem")
        body = {"user_id": user1, "amount": "10.00", "reason": "补偿一"}

        r1 = await client.post(
            "/api/admin/v1/adjustments", json=body, headers={**finance, "Idempotency-Key": "k-1"}
        )
        assert r1.status_code == 201, r1.text
        # 同键同体重放 → 200 + 重放头,同一单
        r2 = await client.post(
            "/api/admin/v1/adjustments", json=body, headers={**finance, "Idempotency-Key": "k-1"}
        )
        assert r2.status_code == 200
        assert r2.headers["x-idempotent-replay"] == "true"
        assert r2.json()["id"] == r1.json()["id"]
        # 同键异体(金额不同)→ 409 指纹不符
        r3 = await client.post(
            "/api/admin/v1/adjustments",
            json={**body, "amount": "20.00"},
            headers={**finance, "Idempotency-Key": "k-1"},
        )
        assert r3.status_code == 409
        assert r3.json()["message_key"] == "adminapi.idempotencyKeyMismatch"
        # 同键同体跨租户 → 新单(作用域 (发起人,租户,键))
        r4 = await client.post(
            "/api/admin/v1/adjustments",
            json={**body, "user_id": u2id},
            headers={**finance, "Idempotency-Key": "k-1"},
        )
        assert r4.status_code == 201, r4.text
        assert r4.json()["id"] != r1.json()["id"]

    async def test_concurrent_review_single_credit(self, client, sm, fake):
        """两名复核人并发 approve 同一单:行锁保证只入账一次。"""
        headers, _uuid, user_id = await _provision_running(client, sm, fake)
        async with sm() as session:
            creator = await create_admin(session, "fin-race-a", "pass1234", "finance")
            r1 = await create_admin(session, "fin-race-b", "pass1234", "finance")
            r2 = await create_admin(session, "fin-race-c", "pass1234", "finance")
            creator_id, r1_id, r2_id = creator.id, r1.id, r2.id
        async with sm() as session:
            adj, _created = await admin_service.create_adjustment(
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

    async def test_reviewer_created_after_adjustment_rejected(self, client, sm, fake):
        """防自建第二账号绕双人复核:复核人必须是调账发起前已存在的账号。

        挂了 = 单个 admin 发起调账后自建新账号复核,双人制衡形同虚设。
        """
        _headers, _uuid, user_id = await _provision_running(client, sm, fake)
        fin_a = await second_admin_headers(sm, client, "fin-late-a")
        resp = await client.post(
            "/api/admin/v1/adjustments",
            json={"user_id": user_id, "amount": "25.50", "reason": "故障补偿"},
            headers=fin_a,
        )
        assert resp.status_code == 201, resp.text
        adj_id = resp.json()["id"]

        # 发起后才创建的账号:不能充当第二复核人
        fin_b = await second_admin_headers(sm, client, "fin-late-b")
        resp = await client.post(
            f"/api/admin/v1/adjustments/{adj_id}/review",
            json={"approve": True},
            headers=fin_b,
        )
        assert resp.status_code == 403
        assert resp.json()["message_key"] == "adminapi.adjustReviewerTooNew"

        # 发起前已存在的账号:正常复核通过
        fin_c = await second_admin_headers(sm, client, "fin-late-c")
        resp = await client.post(
            "/api/admin/v1/adjustments",
            json={"user_id": user_id, "amount": "25.50", "reason": "故障补偿"},
            headers=fin_a,
        )
        adj2_id = resp.json()["id"]
        resp = await client.post(
            f"/api/admin/v1/adjustments/{adj2_id}/review",
            json={"approve": True},
            headers=fin_c,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "approved"

    async def test_create_adjustment_idempotency_key(self, client, sm, fake):
        """调账发起支持 Idempotency-Key:同键重放返回同一单,不开第二张。"""
        from app.modules.adminapi.models import AdminAdjustment

        _headers, _uuid, user_id = await _provision_running(client, sm, fake)
        fin = await second_admin_headers(sm, client, "fin-idem-a")
        keyed = {**fin, "Idempotency-Key": "adj-20260822-01"}
        body = {"user_id": user_id, "amount": "12.00", "reason": "重复提交演练"}
        r1 = await client.post("/api/admin/v1/adjustments", json=body, headers=keyed)
        assert r1.status_code == 201, r1.text
        r2 = await client.post("/api/admin/v1/adjustments", json=body, headers=keyed)
        assert r2.status_code == 200, r2.text
        assert r2.headers["x-idempotent-replay"] == "true"
        assert r2.json()["id"] == r1.json()["id"]
        async with sm() as session:
            rows = (await session.execute(select(AdminAdjustment))).scalars().all()
        assert len(rows) == 1

    async def test_adjustment_amount_strict_decimal(self, client, sm, fake):
        """调账金额契约层严格十进制:科学计数法/超 2 位小数/非数字一律 422,不进服务层。"""
        _headers, _uuid, user_id = await _provision_running(client, sm, fake)
        fin = await second_admin_headers(sm, client, "fin-strict")
        for bad in ("1e2", "1E-3", "10.005", "abc", "1,000.00", "10.", ".5", "--10.00", ""):
            resp = await client.post(
                "/api/admin/v1/adjustments",
                json={"user_id": user_id, "amount": bad, "reason": "严格校验"},
                headers=fin,
            )
            assert resp.status_code == 422, (bad, resp.text)
        for good in ("-10.00", "25.50", "0.01", "-0.01", "100", "99999.99"):
            resp = await client.post(
                "/api/admin/v1/adjustments",
                json={"user_id": user_id, "amount": good, "reason": "严格校验"},
                headers=fin,
            )
            assert resp.status_code == 201, (good, resp.text)


class TestTenantAggregations:
    async def test_scoped_to_page_users(self, client, sm, fake):
        """租户列表的三个按 user 聚合只算本页用户(IN 过滤),不做全表 GROUP BY。

        挂了 = 带 q 检索单租户也全表聚合 balances/ledger/instances,数据量上来后列表页拖垮库。
        """
        from tests.helpers import fund_wallet

        _headers, _uuid, id1 = await _provision_running(client, sm, fake, "13600000061")
        u2 = await register(client, "13600000062")
        id2 = u2["user"]["id"]
        await fund_wallet(sm, id1, "10.00")
        await fund_wallet(sm, id2, "20.00")
        from app.modules.billing import service as billing_service

        async with sm() as session:
            await billing_service.debit(
                session, id2, Decimal("3.00"), type_="consume", allow_negative=True
            )
            await session.commit()

        from app.modules.orchestrator import service as orchestrator_service

        async with sm() as session:
            stats = await orchestrator_service.instance_disk_stats_by_user(session, [id1])
            assert set(stats) == {id1} and stats[id1]["instances"] == 1
            balances = await billing_service.balances_by_user(session, [id1])
            assert set(balances) == {id1}
            consumed = await billing_service.consumed_by_user(session, [id2])
            assert set(consumed) == {id2} and consumed[id2] == Decimal("3.00")


class TestNodesAndReports:
    async def test_port_pool_stats(self, client, sm, fake):
        """端口池水位:assigned=已分配实例数;blocked=撞占标记(周期复检会放回)。"""
        from app.modules.orchestrator.service import block_port

        _headers, _uuid, _user_id = await _provision_running(client, sm, fake)  # 占 1 端口
        await block_port(sm, 31999, reason="test_orphan_endpoint", expected_instance_id=None)
        ah = await admin_headers(sm, client, role="readonly")
        pool = (await client.get("/api/admin/v1/nodes/port-pool", headers=ah)).json()
        assert pool["assigned"] == 1
        assert pool["blocked"] == 1
        assert pool["total"] >= 2

    async def test_oversell_report(self, client, sm, fake):
        _headers, _uuid, _user_id = await _provision_running(client, sm, fake)  # hami 池 50% × 1
        # 报表读台账(node_specs):先跑一轮巡检把 fake 节点写进台账(等价真实环境 60s 巡检)
        from app.modules.nodes.patrol import node_spec_patrol

        await node_spec_patrol(sm)
        ah = await admin_headers(sm, client, role="finance")
        report = (await client.get("/api/admin/v1/reports/oversell", headers=ah)).json()
        hami = next(r for r in report if r["pool"] == "hami")
        assert hami["physical_gpus"] == 32
        assert hami["sold_share"] == 0.5
        assert hami["oversell_ratio"] == round(0.5 / 32, 3)

    async def test_oversell_report_pool_scoped_utilization(self, client, sm, fake):
        """利用率按池加权聚合;无数据的池必须是 null,不得用集群均值冒充。"""
        from app.modules.metering.models import UsageHourly
        from app.modules.nodes.patrol import node_spec_patrol
        from app.modules.orchestrator.models import Instance

        _headers, _uuid, _user_id = await _provision_running(client, sm, fake)  # hami 池实例
        await node_spec_patrol(sm)  # 台账播种:报表物理口径来自 node_specs
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

        # 重放:需原因(与忽略对齐),置回 pending,计数清零
        resp = await client.post(f"/api/admin/v1/outbox/{task_id}/retry", headers=ah)
        assert resp.status_code == 422  # 缺原因直接拒
        resp = await client.post(
            f"/api/admin/v1/outbox/{task_id}/retry",
            json={"reason": "调度抖动已恢复"},
            headers=ah,
        )
        assert resp.status_code == 200
        async with sm() as session:
            task = await session.get(OutboxTask, task_id)
            assert task is not None and task.status == "pending" and task.retries == 0

        # 非 dead 状态不可重放/忽略
        resp = await client.post(
            f"/api/admin/v1/outbox/{task_id}/retry",
            json={"reason": "再试一次"},
            headers=ah,
        )
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


class TestRevenueReport:
    async def test_today_revenue_and_signups(self, client: AsyncClient, sm):
        """营收口径 = 账单归属期(bills_hourly.hour_start),不是 ledger 入账时间。
        tz_offset 缺省 480(东八区):UTC 16:00–24:00 的账归北京次日,不在「今日」。"""
        from app.modules.billing.models import BillHourly

        data = await register(client, "13600000043")
        hour = now_utc().replace(minute=0, second=0, microsecond=0)
        async with sm() as session:
            session.add(
                BillHourly(
                    instance_id=1,
                    user_id=data["user"]["id"],
                    hour_start=hour,
                    seconds_used=3600,
                    unit_price=Decimal("12.5000"),
                    amount=Decimal("12.50"),
                )
            )
            await session.commit()

        ah = await admin_headers(sm, client, role="finance")
        resp = await client.get("/api/admin/v1/reports/revenue", headers=ah)
        assert resp.status_code == 200
        body = resp.json()
        beijing_day_start = (now_utc() + timedelta(hours=8)).replace(
            hour=0, minute=0, second=0, microsecond=0
        ) - timedelta(hours=8)
        expected_today = "12.50" if hour >= beijing_day_start else "0"
        assert body["today_revenue"] == expected_today  # 缺省 tz_offset=480(原默认 0 已修正)
        assert body["month_revenue"] == "12.50"
        assert body["today_signups"] >= 1
        # 显式越界一律 422(±720 上下界)
        resp = await client.get(
            "/api/admin/v1/reports/revenue", params={"tz_offset_minutes": 840}, headers=ah
        )
        assert resp.status_code == 422


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
        notifications = (await client.get("/api/v1/notifications", headers=headers)).json()["items"]
        assert any(n["type"] == "announcement" for n in notifications)

    async def test_publish_bulk_insert_skips_frozen(
        self, client: AsyncClient, sm, monkeypatch: pytest.MonkeyPatch
    ):
        """群发是批量 INSERT(单事务 ⌈N/1000⌉ 条语句),且只触达 active 用户。

        逐用户 INSERT...RETURNING 的 N+1 若回潮,本用例经 monkeypatch 直接失败。
        """
        from app.modules.account import service as account_service
        from app.modules.notify import service as notify_service

        async def _no_per_user_notify(*args, **kwargs):
            raise AssertionError("公告群发不得逐用户调用 notify()")

        monkeypatch.setattr(notify_service, "notify", _no_per_user_notify)

        u1 = await register(client, "13600000043")
        u2 = await register(client, "13600000044")
        async with sm() as session:
            await account_service.admin_set_user_status(session, u2["user"]["id"], "frozen")
            await session.commit()
        ah = await admin_headers(sm, client, role="ops")
        resp = await client.post(
            "/api/admin/v1/announcements",
            json={"title": "批量写入验证", "content": "维护通知"},
            headers=ah,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["reached"] == 1  # 冻结用户不触达
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
        assert {r.user_id for r in rows} == {u1["user"]["id"]}


class TestTenantBillingDrilldown:
    """账单争议处理:管理端必须能看到任一租户的账单明细与资金流水。"""

    async def test_ledger_pagination(self, client, sm):
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
                    allow_negative=True,
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


class TestFreezeStopsInstances:
    async def test_freeze_stops_running_instances(self, client, sm, fake):
        """封禁必须同时停机、停计费。

        只改 status + 撤 token 的话,计费主链路不看用户状态,被封账号会继续跑并继续扣费。
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
        # 回显本次停掉的 running 台数,前端据此提示影响面
        assert resp.json()["instances_stopped"] == 1

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
        listed = (await client.get("/api/admin/v1/instances", headers=h)).json()["items"]
        assert [i["status"] for i in listed if i["uuid"] == uuid] == ["stopping"]
        await drain(sm)
        await reconcile_once(sm)
        listed = (await client.get("/api/admin/v1/instances", headers=h)).json()["items"]
        # 计费边(running→stopping→stopped)已闭合,后续小时不再产生账单
        assert [i["status"] for i in listed if i["uuid"] == uuid] == ["stopped"]

    async def test_unfreeze_does_not_auto_start(self, client, sm, fake):
        """解封不自动开机:解封即批量拉起会立刻又欠费停机。"""
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
        listed = (await client.get("/api/admin/v1/instances", headers=h)).json()["items"]
        assert [i["status"] for i in listed if i["uuid"] == uuid] == ["stopped"]


class TestAdminSearch:
    """客服与财务的第一个日常动作:按手机号找人、按订单号找单、按节点找实例。"""

    async def test_tenant_lookup_by_phone(self, client, sm, fake):
        h = await admin_headers(sm, client)
        await register(client, "13611110001")
        await register(client, "13622220002")

        exact = (
            await client.get("/api/admin/v1/tenants", params={"q": "13611110001"}, headers=h)
        ).json()["items"]
        assert [t["phone_masked"] for t in exact] == ["136****0001"]
        # 只记得后几位也能找到(客服常见情形)
        resp = await client.get("/api/admin/v1/tenants", params={"q": "0002"}, headers=h)
        suffix = resp.json()["items"]
        assert [t["phone_masked"] for t in suffix] == ["136****0002"]
        # 列表仍只回掩码:「查得到」不等于「看得到」
        assert all("phone" not in t or t.get("phone") is None for t in exact)

    async def test_tenant_search_escapes_like_metachars(self, client, sm, fake):
        """q 未转义时一个 % 即拖全表:元字符按字面匹配,正常后缀检索行为不变。"""
        h = await admin_headers(sm, client)
        await register(client, "13611110001")

        pct = (await client.get("/api/admin/v1/tenants", params={"q": "%"}, headers=h)).json()
        assert pct["items"] == []
        underscore = (
            await client.get("/api/admin/v1/tenants", params={"q": "_"}, headers=h)
        ).json()
        assert underscore["items"] == []
        resp = await client.get("/api/admin/v1/tenants", params={"q": "0001"}, headers=h)
        suffix = resp.json()["items"]
        assert [t["phone_masked"] for t in suffix] == ["136****0001"]

    async def test_tenant_search_is_audited(self, client, sm, fake):
        """按号码检索是敏感读:默认只审计写操作,这里必须显式留痕。"""
        from app.core.audit import AuditLog

        h = await admin_headers(sm, client)
        await register(client, "13611110003")
        await client.get("/api/admin/v1/tenants", params={"q": "13611110003"}, headers=h)
        async with sm() as session:
            rows = (
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.action.like("%GET /api/admin/v1/tenants%"))
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1
        assert rows[0].target == "tenant-search:136****0003"  # 审计里也只留掩码

    async def test_plain_tenant_list_is_not_audited(self, client, sm, fake):
        """不带查询的普通列表不落审计,避免写放大 + 表膨胀。"""
        from app.core.audit import AuditLog

        h = await admin_headers(sm, client)
        await client.get("/api/admin/v1/tenants", headers=h)
        async with sm() as session:
            rows = (
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.action.like("%GET /api/admin/v1/tenants%"))
                    )
                )
                .scalars()
                .all()
            )
        assert rows == []

    async def test_instance_lookup_by_node_and_name(self, client, sm, fake):
        h = await admin_headers(sm, client)
        _uh, uuid, user_id = await _provision_running(client, sm, fake, "13611110004")
        by_node = (
            await client.get(
                "/api/admin/v1/instances", params={"node_name": "fake-node-1"}, headers=h
            )
        ).json()["items"]
        assert [i["uuid"] for i in by_node] == [uuid]
        # 「这台 GPU 是谁的」:管理端实例视图带租户与节点
        assert by_node[0]["user_id"] == user_id
        assert by_node[0]["node_name"] == "fake-node-1"
        by_uuid = (
            await client.get("/api/admin/v1/instances", params={"q": uuid[:8]}, headers=h)
        ).json()["items"]
        assert [i["uuid"] for i in by_uuid] == [uuid]
        assert (
            await client.get("/api/admin/v1/instances", params={"node_name": "nope"}, headers=h)
        ).json()["items"] == []

    async def test_order_lookup_by_order_no(self, client, sm, fake):
        from app.modules.billing.models import Order

        h = await admin_headers(sm, client)
        async with sm() as session:
            for no in ("SDL-A", "SDL-B"):
                session.add(
                    Order(
                        order_no=no,
                        user_id=1,
                        amount=Decimal("10.00"),
                        channel="mock",
                        expires_at=now_utc() + timedelta(minutes=30),
                    )
                )
            await session.commit()
        found = (
            await client.get("/api/admin/v1/orders", params={"order_no": "SDL-B"}, headers=h)
        ).json()["items"]
        assert [o["order_no"] for o in found] == ["SDL-B"]


class TestTenantLookupById:
    async def test_numeric_q_hits_user_id(self, client, sm, fake):
        """订单/调账/实例全以 user_id 指代租户:纯数字 q 必须能按 id 精确命中(排在最前)。"""
        h = await admin_headers(sm, client)
        data = await register(client, "13633330003")
        uid = data["user"]["id"]

        resp = await client.get("/api/admin/v1/tenants", params={"q": str(uid)}, headers=h)
        rows = resp.json()["items"]
        assert rows[0]["id"] == uid
        # id 无命中时回落手机号后缀语义,不报错
        assert (
            await client.get("/api/admin/v1/tenants", params={"q": "99999999"}, headers=h)
        ).json()["items"] == []
        # 手机号后缀检索行为不变
        resp = await client.get("/api/admin/v1/tenants", params={"q": "0003"}, headers=h)
        rows = resp.json()["items"]
        assert any(t["id"] == uid for t in rows)


class TestAdjustContext:
    async def test_context_and_unknown_user(self, client, sm, fake):
        """调账前置上下文:掩码手机号 + 当前余额 + 近 3 条流水 + 在跑台数;幽灵 id → 404。"""
        _headers, _uuid, user_id = await _provision_running(client, sm, fake)
        fin = await second_admin_headers(sm, client, "fin-ctx")

        resp = await client.get(f"/api/admin/v1/tenants/{user_id}/adjust-context", headers=fin)
        assert resp.status_code == 200, resp.text
        ctx = resp.json()
        assert ctx["user_id"] == user_id
        assert "****" in ctx["phone_masked"]
        assert ctx["balance"] == "100.00"
        assert ctx["running_instances"] == 1
        assert len(ctx["recent_ledger"]) >= 1  # fund_wallet 的 recharge 入账

        resp = await client.get("/api/admin/v1/tenants/999999/adjust-context", headers=fin)
        assert resp.status_code == 404

    async def test_create_unknown_user_rejected(self, client, sm, fake):
        """钱包会为任意 user_id 凭空建行:发起调账必须先拦住不存在的租户。"""
        fin = await second_admin_headers(sm, client, "fin-ghost")
        resp = await client.post(
            "/api/admin/v1/adjustments",
            json={"user_id": 999999, "amount": "10.00", "reason": "测试"},
            headers=fin,
        )
        assert resp.status_code == 404

    async def test_create_over_cap_rejected(self, client, sm, fake):
        """单笔绝对值上限(ADJUST_MAX_ABS):防手滑多敲零,超出走对公/线下流程。"""
        _headers, _uuid, user_id = await _provision_running(client, sm, fake)
        fin = await second_admin_headers(sm, client, "fin-cap")

        for amount in ("100000.01", "-200000.00"):
            resp = await client.post(
                "/api/admin/v1/adjustments",
                json={"user_id": user_id, "amount": amount, "reason": "超额"},
                headers=fin,
            )
            assert resp.status_code == 400
            assert resp.json()["code"] == "VALIDATION_ERROR"
        # 上限边界值仍放行
        resp = await client.post(
            "/api/admin/v1/adjustments",
            json={"user_id": user_id, "amount": "100000.00", "reason": "边界值"},
            headers=fin,
        )
        assert resp.status_code == 201, resp.text


class TestOverview:
    async def test_exact_counts(self, client, sm, fake):
        """总览聚合:精确 COUNT 口径,替代在截断列表(200/500 条)里数数。"""
        from app.modules.nodes.models import NodeSpec

        _headers, _uuid, user_id = await _provision_running(client, sm, fake)
        async with sm() as session:
            session.add_all(
                [
                    NodeSpec(
                        node_name="gpu-a1",
                        pool_label="hami",
                        gpu_count=8,
                        gpu_used=3,
                        status="Ready",
                        last_seen=now_utc(),
                    ),
                    NodeSpec(
                        node_name="gpu-b1",
                        pool_label="hami",
                        gpu_count=8,
                        gpu_used=0,
                        status="NotReady",
                        last_seen=now_utc(),
                    ),
                    NodeSpec(
                        node_name="gpu-c1",
                        pool_label="kata",
                        gpu_count=4,
                        gpu_used=0,
                        status="Missing",
                        last_seen=now_utc(),
                    ),
                ]
            )
            await session.commit()

        ah = await admin_headers(sm, client, role="readonly")
        resp = await client.get("/api/admin/v1/overview", headers=ah)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["instances_by_status"]["running"] == 1
        assert body["tenants_total"] >= 1
        assert body["paying_tenants"] == 0  # 只有 recharge,没有 consume 流水
        assert body["nodes_total"] == 3
        assert body["nodes_ready"] == 1
        assert body["nodes_missing"] == 1
        hami = next(p for p in body["pools"] if p["pool"] == "hami")
        assert hami["gpu_total"] == 16  # 含 NotReady 节点
        assert hami["ready_gpu_total"] == 8

        # 落一条 consume 流水后,付费租户精确 +1
        async with sm() as session:
            session.add(
                BalanceLedger(
                    user_id=user_id,
                    type="consume",
                    amount=Decimal("-1.00"),
                    balance_after=Decimal("99.00"),
                )
            )
            await session.commit()
        body = (await client.get("/api/admin/v1/overview", headers=ah)).json()
        assert body["paying_tenants"] == 1

    async def test_sku_impact(self, client, sm, fake):
        """改价影响面:该 SKU 当前活跃实例数 / 涉及用户数 / 占用卡数。"""
        from app.modules.orchestrator.models import Instance

        await _provision_running(client, sm, fake)
        async with sm() as session:
            sku_id = (await session.execute(select(Instance.sku_id))).scalar_one()
        ah = await admin_headers(sm, client, role="ops")
        resp = await client.get(f"/api/admin/v1/skus/{sku_id}/impact", headers=ah)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "sku_id": sku_id,
            "active_instances": 1,
            "active_users": 1,
            "active_gpus": 1,
        }


class TestAuditPagination:
    async def test_cursor_turns_page(self, client, sm, fake):
        """审计翻页:cursor=末行 id 的不透明编码,下一页全是更早的行;非法游标 400。"""
        import base64

        await _provision_running(client, sm, fake)  # 产生若干审计行
        ah = await admin_headers(sm, client, role="admin")

        page1 = (await client.get("/api/admin/v1/audit", params={"limit": 2}, headers=ah)).json()
        assert len(page1) == 2
        cursor = base64.urlsafe_b64encode(str(page1[-1]["id"]).encode()).decode()
        page2 = (
            await client.get(
                "/api/admin/v1/audit", params={"limit": 2, "cursor": cursor}, headers=ah
            )
        ).json()
        assert len(page2) >= 1
        assert all(r["id"] < page1[-1]["id"] for r in page2)

        resp = await client.get("/api/admin/v1/audit", params={"cursor": "!!!"}, headers=ah)
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "common.badCursor"


class TestTenantRealnameExposure:
    """实名透出:readonly 脱敏;ops/finance/admin 明文,且含实名字段的响应落敏感读审计。"""

    async def _realname_user(self, client, sm) -> int:
        from app.modules.account import service as account_service

        data = await register(client, "13655550001")
        uid = data["user"]["id"]
        async with sm() as session:
            user = await account_service.get_user(session, uid)
            user.company_name = "北京示例科技有限公司"
            await account_service.submit_real_name(session, user, "张三", "110101199001011234")
        return uid

    async def test_readonly_sees_masked_and_no_audit(self, client, sm, fake):
        """readonly:姓名留姓掩名、企业名留首尾;脱敏响应不落实名读审计(防列表页写放大)。"""
        from app.core.audit import AuditLog

        uid = await self._realname_user(client, sm)
        ro = await admin_headers(sm, client, role="readonly")
        rows = (await client.get("/api/admin/v1/tenants", headers=ro)).json()["items"]
        me = next(t for t in rows if t["id"] == uid)
        assert me["verification_status"] == "verified"
        assert me["id_name"] == "张*"
        assert me["company_name"] == "北京******公司"
        async with sm() as session:
            hits = (
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.target == "tenant-realname:list")
                    )
                )
                .scalars()
                .all()
            )
        assert hits == []

    async def test_ops_sees_plaintext_and_audited(self, client, sm, fake):
        """ops 看明文;响应真含实名字段 → 恰好落一条敏感读审计(内容不进审计,只记条数)。"""
        from app.core.audit import AuditLog

        uid = await self._realname_user(client, sm)
        ah = await admin_headers(sm, client, role="ops")
        rows = (await client.get("/api/admin/v1/tenants", headers=ah)).json()["items"]
        me = next(t for t in rows if t["id"] == uid)
        assert me["verification_status"] == "verified"
        assert me["id_name"] == "张三"
        assert me["company_name"] == "北京示例科技有限公司"
        async with sm() as session:
            hits = (
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.target == "tenant-realname:list")
                    )
                )
                .scalars()
                .all()
            )
        assert len(hits) == 1
        assert hits[0].action == "admin.GET /api/admin/v1/tenants"
        assert hits[0].detail == {"rows": 1}


class TestTenantQuotaOverride:
    """配额覆盖:override 优先于 policy/env;清空恢复默认链;updated_by 落库;readonly 只读。"""

    async def test_override_caps_disks_then_clear_restores(self, client, sm, fake):
        from tests.helpers import create_user_with_key, fund_wallet
        from tests.test_disks import create_disk

        headers, user_id, _key = await create_user_with_key(client, "13655550002")
        await fund_wallet(sm, user_id)
        ah = await admin_headers(sm, client, role="ops")

        resp = await client.put(
            f"/api/admin/v1/tenants/{user_id}/quota",
            json={"max_disks": 1, "note": "防滥用限一块"},
            headers=ah,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["max_disks"] == 1 and body["effective_max_disks"] == 1
        assert body["note"] == "防滥用限一块"

        # updated_by 落库:写覆盖的管理员 id
        from app.modules.adminapi.models import AdminUser

        async with sm() as session:
            admin_id = (
                await session.execute(select(AdminUser.id).where(AdminUser.username == "ops-user"))
            ).scalar_one()
        assert body["updated_by"] == admin_id and body["updated_at"]

        await create_disk(client, headers, name="d1", size_gb=50)
        # 覆盖(1)优先于 env 默认(20):第 2 块被拒
        blocked = await client.post(
            "/api/v1/disks", json={"name": "d2", "size_gb": 50}, headers=headers
        )
        assert blocked.status_code == 400
        assert blocked.json()["message_key"] == "disks.countQuota"
        assert blocked.json()["params"]["max"] == 1

        # 清空覆盖(三项全空)→ 恢复默认链,第 2 块放行
        resp = await client.put(
            f"/api/admin/v1/tenants/{user_id}/quota",
            json={"note": "复核后恢复默认"},
            headers=ah,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["max_disks"] is None and body["effective_max_disks"] == 20
        assert body["updated_by"] is None
        await create_disk(client, headers, name="d2", size_gb=50)

        # 清空操作本身也过审计(写操作中间件 + set_audit_target)
        from app.core.audit import AuditLog

        async with sm() as session:
            rows = (
                (
                    await session.execute(
                        select(AuditLog)
                        .where(
                            AuditLog.action == f"admin.PUT /api/admin/v1/tenants/{user_id}/quota"
                        )
                        .order_by(AuditLog.id)
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 2
        assert rows[-1].detail["after"] == {
            "max_gpus": None,
            "max_instances": None,
            "max_disks": None,
        }

    async def test_readonly_cannot_write_quota(self, client, sm, fake):
        data = await register(client, "13655550003")
        ro = await admin_headers(sm, client, role="readonly")
        resp = await client.put(
            f"/api/admin/v1/tenants/{data['user']['id']}/quota",
            json={"max_disks": 1, "note": "越权尝试"},
            headers=ro,
        )
        assert resp.status_code == 403
        # 读不挡:全角色可见生效值
        resp = await client.get(f"/api/admin/v1/tenants/{data['user']['id']}/quota", headers=ro)
        assert resp.status_code == 200
        assert resp.json()["effective_max_disks"] == 20


class TestAdminInstanceEvents:
    """管理端实例事件时间线:读全角色,按时间倒序,游标分页;非管理端凭据拒绝。"""

    async def test_events_desc_and_cursor(self, client, sm, fake):
        _uh, uuid, _uid = await _provision_running(client, sm, fake)
        ah = await admin_headers(sm, client, role="readonly")

        resp = await client.get(f"/api/admin/v1/instances/{uuid}/events", headers=ah)
        assert resp.status_code == 200, resp.text
        page = resp.json()
        ids = [e["id"] for e in page["items"]]
        assert len(ids) >= 2
        assert ids == sorted(ids, reverse=True)

        p1 = (await client.get(f"/api/admin/v1/instances/{uuid}/events?limit=1", headers=ah)).json()
        assert len(p1["items"]) == 1 and p1["next_cursor"]
        p2 = (
            await client.get(
                f"/api/admin/v1/instances/{uuid}/events?limit=1&cursor={p1['next_cursor']}",
                headers=ah,
            )
        ).json()
        assert len(p2["items"]) >= 1
        assert all(e["id"] < p1["items"][0]["id"] for e in p2["items"])

    async def test_user_token_rejected_and_unknown_uuid(self, client, sm, fake):
        _uh, uuid, _uid = await _provision_running(client, sm, fake)
        data = await register(client, "13655550004")
        # 管理端是独立 JWT audience:用户 token 过不了鉴权依赖(401)
        resp = await client.get(
            f"/api/admin/v1/instances/{uuid}/events",
            headers={"Authorization": f"Bearer {data['access_token']}"},
        )
        assert resp.status_code == 401
        ah = await admin_headers(sm, client, role="ops")
        resp = await client.get("/api/admin/v1/instances/nope-uuid/events", headers=ah)
        assert resp.status_code == 404


class TestAdminListPagination:
    """五个管理端列表端点的游标分页(Page 包装 + next_cursor 走查)与筛选参数。"""

    async def test_tenants_cursor_walk(self, client, sm, fake):
        h = await admin_headers(sm, client)
        for i in range(3):
            await register(client, f"1367777{i:04d}")
        p1 = (await client.get("/api/admin/v1/tenants", params={"limit": 2}, headers=h)).json()
        assert len(p1["items"]) == 2 and p1["next_cursor"]
        p2 = (
            await client.get(
                "/api/admin/v1/tenants",
                params={"limit": 2, "cursor": p1["next_cursor"]},
                headers=h,
            )
        ).json()
        ids1 = {t["id"] for t in p1["items"]}
        assert len(p2["items"]) >= 1
        assert all(t["id"] not in ids1 for t in p2["items"])
        # 降序:第二页 id 全部小于第一页最小 id
        assert max(t["id"] for t in p2["items"]) < min(ids1)

    async def test_instances_cursor_walk(self, client, sm, fake):
        h = await admin_headers(sm, client)
        _h1, uuid1, _u1 = await _provision_running(client, sm, fake, "13677780001")
        _h2, uuid2, _u2 = await _provision_running(client, sm, fake, "13677780002")
        p1 = (await client.get("/api/admin/v1/instances", params={"limit": 1}, headers=h)).json()
        assert len(p1["items"]) == 1 and p1["next_cursor"]
        p2 = (
            await client.get(
                "/api/admin/v1/instances",
                params={"limit": 1, "cursor": p1["next_cursor"]},
                headers=h,
            )
        ).json()
        seen = {p1["items"][0]["uuid"], *(i["uuid"] for i in p2["items"])}
        assert {uuid1, uuid2} <= seen

    async def test_orders_cursor_and_day_filter(self, client, sm, fake):
        """订单:游标走查;day=YYYY-MM-DD 只留当日单(昨日单被滤掉)。"""
        from app.modules.billing.models import Order

        h = await admin_headers(sm, client, role="finance")
        async with sm() as session:
            for i in range(3):
                session.add(
                    Order(
                        order_no=f"SDL-PAGE-{i}",
                        user_id=1,
                        amount=Decimal("10.00"),
                        channel="mock",
                        expires_at=now_utc() + timedelta(minutes=30),
                    )
                )
            await session.commit()
        # 把最旧的一单改到昨天:day=today 须滤掉它,day=yesterday 只剩它
        async with sm() as session:
            oldest = (
                await session.execute(select(Order).where(Order.order_no == "SDL-PAGE-0"))
            ).scalar_one()
            oldest.created_at = now_utc() - timedelta(days=1)
            await session.commit()

        today = now_utc().date().isoformat()
        today_rows = (
            await client.get("/api/admin/v1/orders", params={"day": today}, headers=h)
        ).json()["items"]
        assert {o["order_no"] for o in today_rows} == {"SDL-PAGE-1", "SDL-PAGE-2"}
        yesterday = (now_utc() - timedelta(days=1)).date().isoformat()
        y_rows = (
            await client.get("/api/admin/v1/orders", params={"day": yesterday}, headers=h)
        ).json()["items"]
        assert [o["order_no"] for o in y_rows] == ["SDL-PAGE-0"]
        # 非法日期格式 → 400(与对账端点同口径)
        bad = await client.get("/api/admin/v1/orders", params={"day": "2026-13-99"}, headers=h)
        assert bad.status_code == 400

        p1 = (await client.get("/api/admin/v1/orders", params={"limit": 2}, headers=h)).json()
        assert len(p1["items"]) == 2 and p1["next_cursor"]
        p2 = (
            await client.get(
                "/api/admin/v1/orders",
                params={"limit": 2, "cursor": p1["next_cursor"]},
                headers=h,
            )
        ).json()
        assert [o["order_no"] for o in p2["items"]] == ["SDL-PAGE-0"]

    async def test_adjustments_cursor_and_filters(self, client, sm, fake):
        """调账:status/user_id 过滤 + 游标走查。"""
        from app.modules.adminapi.models import AdminUser

        _headers, _uuid, user_id = await _provision_running(client, sm, fake, "13677780003")
        fin = await second_admin_headers(sm, client, "fin-page")
        async with sm() as session:
            creator = (
                await session.execute(select(AdminUser.id).where(AdminUser.username == "fin-page"))
            ).scalar_one()
            for i in range(3):
                await admin_service.create_adjustment(
                    session,
                    user_id=user_id,
                    amount="1.00",
                    reason=f"分页走查 {i}",
                    created_by=creator,
                )
        p1 = (
            await client.get("/api/admin/v1/adjustments", params={"limit": 2}, headers=fin)
        ).json()
        assert len(p1["items"]) == 2 and p1["next_cursor"]
        p2 = (
            await client.get(
                "/api/admin/v1/adjustments",
                params={"limit": 2, "cursor": p1["next_cursor"]},
                headers=fin,
            )
        ).json()
        assert len(p2["items"]) == 1 and p2["next_cursor"] is None
        # status / user_id 过滤
        pending = (
            await client.get("/api/admin/v1/adjustments", params={"status": "pending"}, headers=fin)
        ).json()["items"]
        assert len(pending) == 3
        approved = (
            await client.get(
                "/api/admin/v1/adjustments", params={"status": "approved"}, headers=fin
            )
        ).json()["items"]
        assert approved == []
        by_user = (
            await client.get("/api/admin/v1/adjustments", params={"user_id": user_id}, headers=fin)
        ).json()["items"]
        assert len(by_user) == 3
        ghost = (
            await client.get("/api/admin/v1/adjustments", params={"user_id": 999999}, headers=fin)
        ).json()["items"]
        assert ghost == []

    async def test_refunds_cursor_smoke(self, client, sm, fake):
        fin = await second_admin_headers(sm, client, "fin-page2")
        page = (await client.get("/api/admin/v1/refunds", headers=fin)).json()
        assert page["items"] == [] and page["next_cursor"] is None
