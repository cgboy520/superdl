"""管理端运营 API:租户/调账双复核/节点与超卖报表/审计检索/死信重放/收入报表/公告。"""

import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.core.audit import AuditLog
from app.core.errors import AppError, ErrorCode
from app.core.outbox import OutboxTask
from app.core.timeutil import now_utc
from app.modules.account.models import User
from app.modules.adminapi import finance_service
from app.modules.adminapi.auth_service import create_admin
from app.modules.adminapi.models import AdminAdjustment, AdminUser
from app.modules.adminapi.router_ops import large_policy_moves
from app.modules.billing.models import BalanceLedger
from app.modules.notify.models import Notification
from app.modules.orchestrator.reconciler import reconcile_once
from tests.helpers import (
    admin_headers,
    as_handle,
    create_disk,
    create_user_with_key,
    drain,
    fund_wallet,
    funded_user,
    provision_running,
    register,
    seed_node_spec,
    set_platform_setting,
    user_headers_with_id,
)

pytestmark = pytest.mark.usefixtures("fake")


async def _season_reviewer(
    sm, username: str, *, days: int = 2, history: str | None = "admin.POST /api/admin/v1/tenants"
) -> None:
    """把管理员建号时间回拨 days 天,并按 history 补一条成功管理操作审计行(None = 不补)。"""
    async with sm() as session:
        admin = (
            await session.execute(select(AdminUser).where(AdminUser.username == username))
        ).scalar_one()
        admin.created_at = now_utc() - timedelta(days=days)
        if history is not None:
            session.add(
                AuditLog(
                    actor_type="admin",
                    actor_id=str(admin.id),
                    action=history,
                    target="tenant:1",
                    result=200,
                    created_at=now_utc() - timedelta(days=1),
                )
            )
        await session.commit()


async def _review(client, adj_id: int, headers: dict, *, approve: bool = True, comment=None):
    body: dict = {"approve": approve}
    if comment is not None:
        body["comment"] = comment
    return await client.post(
        f"/api/admin/v1/adjustments/{adj_id}/review", json=body, headers=headers
    )


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


class TestAdjustments:
    async def test_dual_review_flow(self, client, sm, fake):
        headers, _uuid, user_id = await provision_running(client, sm, fake)
        finance_a = await admin_headers(sm, client, role="finance", username="fin-a")
        finance_b = await admin_headers(sm, client, role="finance", username="fin-b")
        await _season_reviewer(sm, "fin-b")

        resp = await client.post(
            "/api/admin/v1/adjustments",
            json={"user_id": user_id, "amount": "25.50", "reason": "GPU 故障补偿"},
            headers=finance_a,
        )
        assert resp.status_code == 201, resp.text
        adj_id = resp.json()["id"]

        resp = await client.post(
            f"/api/admin/v1/adjustments/{adj_id}/review",
            json={"approve": True},
            headers=finance_a,
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ADMIN_SECOND_REVIEW_REQUIRED"

        resp = await client.post(
            f"/api/admin/v1/adjustments/{adj_id}/review",
            json={"approve": True, "comment": "属实"},
            headers=finance_b,
        )
        assert resp.json()["status"] == "approved"
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "125.50"

        resp = await client.post(
            f"/api/admin/v1/adjustments/{adj_id}/review",
            json={"approve": True},
            headers=finance_b,
        )
        assert resp.status_code == 409

    async def test_negative_adjustment_and_reject(self, client, sm, fake):
        headers, _uuid, user_id = await provision_running(client, sm, fake)
        fin_a = await admin_headers(sm, client, role="finance", username="fin-c")
        fin_b = await admin_headers(sm, client, role="finance", username="fin-d")
        await _season_reviewer(sm, "fin-d")

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
        assert w["balance"] == "100.00"

    async def test_idempotency_scope_and_fingerprint(self, client, sm, fake):
        """同键同体重放 → replay;同键异体 → 409;同键同体跨租户 → 各开各的单。"""
        _h1, _u1, user1 = await provision_running(client, sm, fake)
        _h2, u2id, _k2 = await create_user_with_key(client, "13900000141")
        finance = await admin_headers(sm, client, role="finance", username="fin-idem")
        body = {"user_id": user1, "amount": "10.00", "reason": "补偿一"}

        r1 = await client.post(
            "/api/admin/v1/adjustments", json=body, headers={**finance, "Idempotency-Key": "k-1"}
        )
        assert r1.status_code == 201, r1.text
        r2 = await client.post(
            "/api/admin/v1/adjustments", json=body, headers={**finance, "Idempotency-Key": "k-1"}
        )
        assert r2.status_code == 200
        assert r2.headers["x-idempotent-replay"] == "true"
        assert r2.json()["id"] == r1.json()["id"]
        async with sm() as session:
            assert len((await session.execute(select(AdminAdjustment))).scalars().all()) == 1
        r3 = await client.post(
            "/api/admin/v1/adjustments",
            json={**body, "amount": "20.00"},
            headers={**finance, "Idempotency-Key": "k-1"},
        )
        assert r3.status_code == 409
        assert r3.json()["message_key"] == "common.idempotencyKeyMismatch"
        r4 = await client.post(
            "/api/admin/v1/adjustments",
            json={**body, "user_id": u2id},
            headers={**finance, "Idempotency-Key": "k-1"},
        )
        assert r4.status_code == 201, r4.text
        assert r4.json()["id"] != r1.json()["id"]

    async def test_concurrent_review_single_credit(self, client, sm, fake):
        """两名复核人并发 approve 同一单:行锁保证只入账一次。"""
        headers, _uuid, user_id = await provision_running(client, sm, fake)
        async with sm() as session:
            creator = await create_admin(session, "fin-race-a", "pass1234", "finance")
            r1 = await create_admin(session, "fin-race-b", "pass1234", "finance")
            r2 = await create_admin(session, "fin-race-c", "pass1234", "finance")
            creator_id, r1_id, r2_id = creator.id, r1.id, r2.id
        await _season_reviewer(sm, "fin-race-b")
        await _season_reviewer(sm, "fin-race-c")
        async with sm() as session:
            adj, _created = await finance_service.create_adjustment(
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
                    await finance_service.review_adjustment(
                        session, adj_id, approve=True, reviewer_id=reviewer_id, comment=None
                    )
                    return "approved"
                except AppError as exc:
                    return str(exc.code)

        results = await asyncio.gather(review(r1_id), review(r2_id))
        assert sorted(results) == [str(ErrorCode.CONFLICT), "approved"]
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "110.00"
        async with sm() as session:
            entries = (
                (await session.execute(select(BalanceLedger).where(BalanceLedger.type == "adjust")))
                .scalars()
                .all()
            )
        assert len(entries) == 1

    async def test_reviewer_created_after_adjustment_rejected(self, client, sm, fake):
        """复核人账号须早于调账发起 24 小时创建:发起后建号与 23 小时前建号都拒,25 小时前放行。"""
        _headers, _uuid, user_id = await provision_running(client, sm, fake)
        fin_a = await admin_headers(sm, client, role="finance", username="fin-late-a")
        resp = await client.post(
            "/api/admin/v1/adjustments",
            json={"user_id": user_id, "amount": "25.50", "reason": "故障补偿"},
            headers=fin_a,
        )
        assert resp.status_code == 201, resp.text
        adj_id = resp.json()["id"]

        fin_b = await admin_headers(sm, client, role="finance", username="fin-late-b")
        resp = await _review(client, adj_id, fin_b)
        assert resp.status_code == 403
        assert resp.json()["message_key"] == "adminapi.adjustReviewerTooNew"

        async with sm() as session:
            await session.execute(
                update(AdminUser)
                .where(AdminUser.username == "fin-late-b")
                .values(created_at=now_utc() - timedelta(hours=23))
            )
            await session.commit()
        resp = await _review(client, adj_id, fin_b)
        assert resp.status_code == 403
        assert resp.json()["message_key"] == "adminapi.adjustReviewerTooNew"

        fin_c = await admin_headers(sm, client, role="finance", username="fin-late-c")
        await _season_reviewer(sm, "fin-late-c", days=0)
        async with sm() as session:
            await session.execute(
                update(AdminUser)
                .where(AdminUser.username == "fin-late-c")
                .values(created_at=now_utc() - timedelta(hours=25))
            )
            await session.commit()
        resp = await _review(client, adj_id, fin_c)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "approved"

    async def test_reviewer_without_prior_admin_action_rejected(self, client, sm, fake):
        """先建小号、后发起:老账号但从未做过管理操作(或只做过复核)的复核人不构成独立复核。"""
        _headers, _uuid, user_id = await provision_running(client, sm, fake)
        fin_a = await admin_headers(sm, client, role="finance", username="fin-idle-a")
        fin_b = await admin_headers(sm, client, role="finance", username="fin-idle-b")
        await _season_reviewer(sm, "fin-idle-b", history=None)
        fin_c = await admin_headers(sm, client, role="finance", username="fin-idle-c")
        await _season_reviewer(
            sm, "fin-idle-c", history="admin.POST /api/admin/v1/adjustments/7/review"
        )
        fin_d = await admin_headers(sm, client, role="finance", username="fin-idle-d")
        await _season_reviewer(sm, "fin-idle-d", history="admin.GET /api/admin/v1/audit")

        resp = await client.post(
            "/api/admin/v1/adjustments",
            json={"user_id": user_id, "amount": "25.50", "reason": "故障补偿"},
            headers=fin_a,
        )
        adj_id = resp.json()["id"]
        for headers in (fin_b, fin_c):
            resp = await _review(client, adj_id, headers)
            assert resp.status_code == 403, resp.text
            assert resp.json()["message_key"] == "adminapi.reviewerNotIndependent"
        async with sm() as session:
            adj = await session.get(AdminAdjustment, adj_id)
            assert adj is not None and adj.status == "pending" and adj.reviewed_by is None
        resp = await _review(client, adj_id, fin_d)
        assert resp.status_code == 200, resp.text

    async def test_reviewer_history_after_adjustment_does_not_count(self, client, sm, fake):
        """只有调账发起之前的成功操作算数:发起后才有的操作与失败(4xx)操作都不算。"""
        _headers, _uuid, user_id = await provision_running(client, sm, fake)
        fin_a = await admin_headers(sm, client, role="finance", username="fin-after-a")
        fin_b = await admin_headers(sm, client, role="finance", username="fin-after-b")
        await _season_reviewer(sm, "fin-after-b", history=None)
        resp = await client.post(
            "/api/admin/v1/adjustments",
            json={"user_id": user_id, "amount": "25.50", "reason": "故障补偿"},
            headers=fin_a,
        )
        adj_id = resp.json()["id"]
        async with sm() as session:
            b = (
                await session.execute(select(AdminUser).where(AdminUser.username == "fin-after-b"))
            ).scalar_one()
            session.add(
                AuditLog(
                    actor_type="admin",
                    actor_id=str(b.id),
                    action="admin.POST /api/admin/v1/tenants/1/freeze",
                    result=403,
                    created_at=now_utc() - timedelta(days=1),
                )
            )
            session.add(
                AuditLog(
                    actor_type="admin",
                    actor_id=str(b.id),
                    action="admin.POST /api/admin/v1/announcements",
                    result=201,
                    created_at=now_utc() + timedelta(minutes=1),
                )
            )
            await session.commit()
        resp = await _review(client, adj_id, fin_b)
        assert resp.status_code == 403
        assert resp.json()["message_key"] == "adminapi.reviewerNotIndependent"

    async def test_adjustment_amount_strict_decimal(self, client, sm, fake):
        """调账金额严格十进制:科学计数法/超 2 位小数/非数字一律 422。"""
        _headers, _uuid, user_id = await provision_running(client, sm, fake)
        fin = await admin_headers(sm, client, role="finance", username="fin-strict")
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


class TestPolicyChangeAlert:
    def test_large_policy_moves_only_on_sensitive_keys(self):
        before = {"spot_discount_pct": "40", "disk_grace_days": "7", "disk_min_gb": "10"}
        after = {"spot_discount_pct": "60", "disk_grace_days": "3", "disk_min_gb": "100"}
        assert large_policy_moves(before, after, after) == [
            "disk_grace_days: 7 → 3",
            "spot_discount_pct: 40 → 60",
        ]
        assert large_policy_moves(before, {**after, "spot_discount_pct": "50"}, ["x"]) == []
        assert large_policy_moves({}, after, after) == []

    async def test_policy_write_alerts_on_large_move_and_rate_limits(self, client, sm, fake):
        """敏感策略键相对变化 ≥50% 落 critical 告警(列出键);<50% 不告警;写入口每管理员 20 次/时。"""
        ah = await admin_headers(sm, client, role="admin")
        resp = await client.put(
            "/api/admin/v1/policies",
            json={"updates": {"spot_discount_pct": "50"}, "reason": "微调"},
            headers=ah,
        )
        assert resp.status_code == 200, resp.text
        policy_alerts = select(Notification).where(
            Notification.type == "admin_alert", Notification.title == "策略参数大幅调整"
        )
        async with sm() as session:
            assert (await session.execute(policy_alerts)).scalar_one_or_none() is None

        resp = await client.put(
            "/api/admin/v1/policies",
            json={
                "updates": {
                    "spot_discount_pct": "80",
                    "disk_frozen_days": "10",
                    "disk_min_gb": "20",
                },
                "reason": "大调",
            },
            headers=ah,
        )
        assert resp.status_code == 200, resp.text
        async with sm() as session:
            alert = (await session.execute(policy_alerts)).scalar_one()
        assert alert.severity == "critical"
        assert "spot_discount_pct: 50 → 80" in alert.content
        assert "disk_frozen_days: 30 → 10" in alert.content
        assert "disk_min_gb" not in alert.content and "大调" in alert.content

        for _ in range(18):
            resp = await client.put(
                "/api/admin/v1/policies",
                json={"updates": {"disk_min_gb": "20"}, "reason": "限流用例"},
                headers=ah,
            )
            assert resp.status_code == 200, resp.text
        resp = await client.put(
            "/api/admin/v1/policies",
            json={"updates": {"disk_min_gb": "20"}, "reason": "限流用例"},
            headers=ah,
        )
        assert resp.status_code == 429 and resp.json()["code"] == "RATE_LIMITED"


class TestTenantAggregations:
    async def test_tenant_rows_carry_own_aggregates(self, client, sm, fake):
        """租户列表每行的余额/累计消费/实例数按租户聚合,手机号只回掩码,
        缺省金额按 2 位小数字符串出参。"""
        from app.modules.billing import service as billing_service

        _headers, _uuid, id1 = await provision_running(client, sm, fake, "13600000061")
        id2 = (await register(client, "13600000062"))["user"]["id"]
        id3 = (await register(client, "13600000063"))["user"]["id"]
        await fund_wallet(sm, id2, "20.00")
        async with sm() as session:
            await billing_service.debit(
                session, id2, Decimal("3.00"), type_="consume", allow_negative=True
            )
            await session.commit()

        ah = await admin_headers(sm, client, role="ops")
        rows = (await client.get("/api/admin/v1/tenants", headers=ah)).json()["items"]
        by_id = {t["id"]: t for t in rows}
        assert (
            by_id[id1]["email_masked"] == "u***@test.local" and by_id[id1]["phone_masked"] is None
        )
        assert by_id[id1]["instances"] == 1 and by_id[id1]["balance"] == "100.00"
        assert by_id[id1]["total_consumed"] == "0.00"
        assert by_id[id2]["instances"] == 0 and by_id[id2]["balance"] == "17.00"
        assert by_id[id2]["total_consumed"] == "3.00"
        assert by_id[id3]["balance"] == "0.00" and by_id[id3]["total_consumed"] == "0.00"


class TestNodesAndReports:
    async def test_port_pool_stats(self, client, sm, fake):
        """端口池水位:assigned=已分配实例数;blocked=撞占标记(周期复检会放回)。"""
        from app.modules.orchestrator.ports import block_port

        _headers, _uuid, _user_id = await provision_running(client, sm, fake)
        await block_port(sm, 31999, reason="test_orphan_endpoint", expected_instance_id=None)
        ah = await admin_headers(sm, client, role="readonly")
        pool = (await client.get("/api/admin/v1/nodes/port-pool", headers=ah)).json()
        assert pool["assigned"] == 1
        assert pool["blocked"] == 1
        assert pool["total"] >= 2

    async def test_oversell_report(self, client, sm, fake):
        _headers, _uuid, _user_id = await provision_running(client, sm, fake)
        from app.modules.nodes.patrol import node_spec_patrol

        await node_spec_patrol(sm)
        ah = await admin_headers(sm, client, role="finance")
        report = (await client.get("/api/admin/v1/reports/oversell", headers=ah)).json()
        hami = next(r for r in report if r["pool"] == "hami")
        assert hami["sold_share"] == 0.5
        assert hami["oversell_ratio"] == round(0.5 / 32, 3)

    async def test_oversell_report_pool_scoped_utilization(self, client, sm, fake):
        """利用率按池加权聚合;无数据的池为 null。"""
        from app.modules.metering.models import UsageHourly
        from app.modules.nodes.patrol import node_spec_patrol
        from app.modules.orchestrator.models import Instance

        _headers, _uuid, _user_id = await provision_running(client, sm, fake)
        await node_spec_patrol(sm)
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
        assert by_pool["hami"]["util_avg_24h"] == 45.0
        assert by_pool["kata"]["util_avg_24h"] is None
        assert by_pool["mig"]["util_avg_24h"] is None

    async def test_audit_search(self, client, sm, fake):
        _headers, _uuid, _user_id = await provision_running(client, sm, fake)
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

        resp = await client.post(
            f"/api/admin/v1/outbox/{task_id}/retry",
            json={"reason": "调度抖动已恢复"},
            headers=ah,
        )
        assert resp.status_code == 200
        async with sm() as session:
            task = await session.get(OutboxTask, task_id)
            assert task is not None and task.status == "pending" and task.retries == 0

        resp = await client.post(
            f"/api/admin/v1/outbox/{task_id}/retry",
            json={"reason": "再试一次"},
            headers=ah,
        )
        assert resp.status_code == 409

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
        """营收按账单归属期(bills_hourly.hour_start)计;tz_offset 缺省 480。"""
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
        assert body["today_revenue"] == expected_today
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

        headers = {"Authorization": f"Bearer {u1['access_token']}"}
        notifications = (await client.get("/api/v1/notifications", headers=headers)).json()["items"]
        assert any(n["type"] == "announcement" for n in notifications)

    async def test_publish_bulk_insert_skips_frozen(
        self, client: AsyncClient, sm, monkeypatch: pytest.MonkeyPatch
    ):
        """群发是批量 INSERT(单事务 ⌈N/1000⌉ 条语句),只触达 active 用户。"""
        from app.modules.account import service as account_service
        from app.modules.notify import service as notify_service

        async def _no_per_user_notify(session, user_id, *args, **kwargs):
            if user_id is not None:
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
        assert resp.json()["reached"] == 1
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
    """管理端可看任一租户的账单明细与资金流水。"""

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


class TestFreezeStopsInstances:
    async def test_freeze_stops_running_instances(self, client, sm, fake):
        """封禁同时停机、停计费。"""
        h = await admin_headers(sm, client)
        user_headers, uuid, user_id = await provision_running(client, sm, fake, "13600000090")

        resp = await client.post(
            f"/api/admin/v1/tenants/{user_id}/freeze",
            json={"reason": "疑似挖矿"},
            headers=h,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "frozen"
        assert resp.json()["instances_stopped"] == 1
        assert (await client.get("/api/v1/me", headers=user_headers)).status_code == 403

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

        listed = (await client.get("/api/admin/v1/instances", headers=h)).json()["items"]
        assert [i["status"] for i in listed if i["uuid"] == uuid] == ["stopping"]
        await drain(sm)
        await reconcile_once(sm)
        listed = (await client.get("/api/admin/v1/instances", headers=h)).json()["items"]
        assert [i["status"] for i in listed if i["uuid"] == uuid] == ["stopped"]

    async def test_unfreeze_does_not_auto_start(self, client, sm, fake):
        """解封不自动开机。"""
        h = await admin_headers(sm, client)
        _uh, uuid, user_id = await provision_running(client, sm, fake, "13600000091")
        await client.post(
            f"/api/admin/v1/tenants/{user_id}/freeze", json={"reason": "核查"}, headers=h
        )
        await drain(sm)
        await reconcile_once(sm)
        resp = await client.post(
            f"/api/admin/v1/tenants/{user_id}/unfreeze", json={"reason": "核查完毕"}, headers=h
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"
        listed = (await client.get("/api/admin/v1/instances", headers=h)).json()["items"]
        assert [i["status"] for i in listed if i["uuid"] == uuid] == ["stopped"]


class TestAdminSearch:
    """按手机号找人、按订单号找单、按节点找实例。"""

    async def test_tenant_lookup_by_email_and_phone(self, client, sm, fake):
        h = await admin_headers(sm, client)
        a = (await register(client, "13611110001"))["user"]["id"]
        b = (await register(client, "13622220002"))["user"]["id"]
        async with sm() as session:
            await session.execute(update(User).where(User.id == b).values(phone="+8613622220002"))
            await session.commit()

        exact = (
            await client.get(
                "/api/admin/v1/tenants", params={"q": as_handle("13611110001")}, headers=h
            )
        ).json()["items"]
        assert [t["id"] for t in exact] == [a]
        assert exact[0]["email_masked"] == "u***@test.local"
        prefix = (
            await client.get("/api/admin/v1/tenants", params={"q": "u1362222"}, headers=h)
        ).json()["items"]
        assert [t["id"] for t in prefix] == [b]
        by_phone = (
            await client.get("/api/admin/v1/tenants", params={"q": "+8613622220002"}, headers=h)
        ).json()["items"]
        assert [t["id"] for t in by_phone] == [b] and by_phone[0]["phone_masked"] == "+86****0002"
        suffix = (
            await client.get("/api/admin/v1/tenants", params={"q": "0002"}, headers=h)
        ).json()["items"]
        assert [t["id"] for t in suffix] == [b]

    async def test_tenant_search_escapes_like_metachars(self, client, sm, fake):
        """q 的 LIKE 元字符按字面匹配,支持邮箱前缀检索。"""
        h = await admin_headers(sm, client)
        a = (await register(client, "13611110001"))["user"]["id"]

        pct = (await client.get("/api/admin/v1/tenants", params={"q": "%"}, headers=h)).json()
        assert pct["items"] == []
        underscore = (
            await client.get("/api/admin/v1/tenants", params={"q": "_"}, headers=h)
        ).json()
        assert underscore["items"] == []
        resp = await client.get("/api/admin/v1/tenants", params={"q": "u1361111"}, headers=h)
        assert [t["id"] for t in resp.json()["items"]] == [a]

    async def test_tenant_order_asc_desc_with_cursor(self, client, sm, fake):
        """注册先后(id)正/倒序:游标语义随方向翻转,翻页不重不漏。"""
        h = await admin_headers(sm, client)
        await register(client, "13655510001")
        await register(client, "13655510002")
        await register(client, "13655510003")

        desc = (await client.get("/api/admin/v1/tenants", headers=h)).json()["items"]
        assert [t["id"] for t in desc] == sorted((t["id"] for t in desc), reverse=True)
        page1 = (
            await client.get(
                "/api/admin/v1/tenants", params={"order": "asc", "limit": 2}, headers=h
            )
        ).json()
        assert [t["id"] for t in page1["items"]] == sorted(t["id"] for t in page1["items"])
        page2 = (
            await client.get(
                "/api/admin/v1/tenants",
                params={"order": "asc", "limit": 2, "cursor": page1["next_cursor"]},
                headers=h,
            )
        ).json()
        asc_ids = [t["id"] for t in page1["items"] + page2["items"]]
        assert asc_ids == sorted(asc_ids)
        assert len(asc_ids) == len(set(asc_ids))

    async def test_tenant_search_is_audited(self, client, sm, fake):
        """按号码检索是敏感读,显式落审计。"""
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
        assert rows[0].target == "tenant-search:136****0003"

    async def test_plain_tenant_list_is_not_audited(self, client, sm, fake):
        """不带查询的普通列表不落审计。"""
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
        _uh, uuid, user_id = await provision_running(client, sm, fake, "13611110004")
        by_node = (
            await client.get(
                "/api/admin/v1/instances", params={"node_name": "fake-node-1"}, headers=h
            )
        ).json()["items"]
        assert [i["uuid"] for i in by_node] == [uuid]
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
        """纯数字 q 按 user_id 精确命中(排在最前)。"""
        h = await admin_headers(sm, client)
        data = await register(client, "13633330003")
        uid = data["user"]["id"]
        async with sm() as session:
            await session.execute(update(User).where(User.id == uid).values(phone="+8613633330003"))
            await session.commit()

        resp = await client.get("/api/admin/v1/tenants", params={"q": str(uid)}, headers=h)
        rows = resp.json()["items"]
        assert rows[0]["id"] == uid
        assert (
            await client.get("/api/admin/v1/tenants", params={"q": "99999999"}, headers=h)
        ).json()["items"] == []
        resp = await client.get("/api/admin/v1/tenants", params={"q": "0003"}, headers=h)
        rows = resp.json()["items"]
        assert any(t["id"] == uid for t in rows)


class TestAdjustContext:
    async def test_context_and_unknown_user(self, client, sm, fake):
        """调账前置上下文:掩码手机号 + 当前余额 + 近 3 条流水 + 在跑台数;幽灵 id → 404。"""
        _headers, _uuid, user_id = await provision_running(client, sm, fake)
        fin = await admin_headers(sm, client, role="finance", username="fin-ctx")

        resp = await client.get(f"/api/admin/v1/tenants/{user_id}/adjust-context", headers=fin)
        assert resp.status_code == 200, resp.text
        ctx = resp.json()
        assert ctx["user_id"] == user_id
        assert ctx["email_masked"] == "u***@test.local"
        assert ctx["balance"] == "100.00"
        assert ctx["running_instances"] == 1
        assert len(ctx["recent_ledger"]) >= 1

        resp = await client.get("/api/admin/v1/tenants/999999/adjust-context", headers=fin)
        assert resp.status_code == 404

    async def test_create_unknown_user_rejected(self, client, sm, fake):
        """对不存在的租户发起调账 → 404。"""
        fin = await admin_headers(sm, client, role="finance", username="fin-ghost")
        resp = await client.post(
            "/api/admin/v1/adjustments",
            json={"user_id": 999999, "amount": "10.00", "reason": "测试"},
            headers=fin,
        )
        assert resp.status_code == 404

    async def test_create_over_cap_rejected(self, client, sm, fake):
        """单笔绝对值上限 ADJUST_MAX_ABS。"""
        _headers, user_id = await user_headers_with_id(client, "13900000772")
        fin = await admin_headers(sm, client, role="finance", username="fin-cap")

        for amount in ("100000.01", "-200000.00"):
            resp = await client.post(
                "/api/admin/v1/adjustments",
                json={"user_id": user_id, "amount": amount, "reason": "超额"},
                headers=fin,
            )
            assert resp.status_code == 400
            assert resp.json()["code"] == "VALIDATION_ERROR"
        resp = await client.post(
            "/api/admin/v1/adjustments",
            json={"user_id": user_id, "amount": "100000.00", "reason": "边界值"},
            headers=fin,
        )
        assert resp.status_code == 201, resp.text


class TestOverview:
    async def test_exact_counts(self, client, sm, fake):
        """总览聚合按精确 COUNT。"""
        _headers, _uuid, user_id = await provision_running(client, sm, fake)
        await seed_node_spec(sm, node_name="gpu-a1", pool_label="hami", gpu_count=8, gpu_used=3)
        await seed_node_spec(
            sm, node_name="gpu-b1", pool_label="hami", gpu_count=8, gpu_used=0, status="NotReady"
        )
        await seed_node_spec(
            sm, node_name="gpu-c1", pool_label="kata", gpu_count=4, gpu_used=0, status="Missing"
        )

        ah = await admin_headers(sm, client, role="readonly")
        resp = await client.get("/api/admin/v1/overview", headers=ah)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["instances_by_status"]["running"] == 1
        assert body["tenants_total"] >= 1
        assert body["paying_tenants"] == 0
        assert body["nodes_total"] == 3
        assert body["nodes_ready"] == 1
        assert body["nodes_missing"] == 1
        hami = next(p for p in body["pools"] if p["pool"] == "hami")
        assert hami["gpu_total"] == 16
        assert hami["ready_gpu_total"] == 8

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

        await provision_running(client, sm, fake)
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
    async def test_bad_cursor_400(self, client, sm):
        """非法游标 400。"""
        ah = await admin_headers(sm, client, role="admin")

        resp = await client.get("/api/admin/v1/audit", params={"cursor": "!!!"}, headers=ah)
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "common.badCursor"


class TestTenantRealnameExposure:
    """实名透出:默认脱敏;reveal + reason 看明文(readonly 不可),明文读落敏感读审计。"""

    async def _realname_user(self, client, sm) -> int:
        """开启 real_name_enabled 并注入恒过的假渠道,经正式提交路径落实名字段。"""
        from app.core.config import get_settings
        from app.modules.account import service as account_service
        from app.modules.account.kyc import KycResult, KycSubject, set_kyc_provider

        class _Pass:
            name = "fake"

            async def verify(self, subject: KycSubject) -> KycResult:
                return KycResult(True, "fake", identity_key=subject.identity_number)

        data = await register(client, "13655550001")
        uid = data["user"]["id"]
        async with sm() as session:
            await session.execute(update(User).where(User.id == uid).values(phone="+8613655550001"))
            await session.commit()
        set_kyc_provider(_Pass())
        settings = get_settings()
        previous_profile = settings.compliance_profile
        settings.compliance_profile = "cn"
        try:
            await set_platform_setting(sm, "real_name_enabled", "true")
            async with sm() as session:
                user = await account_service.get_user(session, uid)
                await account_service.submit_kyc(session, user, "张三", "110101199001011237")
        finally:
            set_kyc_provider(None)
            settings.compliance_profile = previous_profile
        return uid

    async def test_default_masked_for_all_roles_and_no_audit(self, client, sm, fake):
        """默认(任意角色):姓名留姓掩名;脱敏响应不落实名读审计。"""
        from app.core.audit import AuditLog

        uid = await self._realname_user(client, sm)
        for role in ("readonly", "ops", "finance"):
            headers = await admin_headers(sm, client, role=role, username=f"rn-{role}")
            rows = (await client.get("/api/admin/v1/tenants", headers=headers)).json()["items"]
            me = next(t for t in rows if t["id"] == uid)
            assert me["kyc_status"] == "verified"
            assert me["kyc_name"] == "张*", role
        async with sm() as session:
            hits = (
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.target.like("tenant-realname:%"))
                    )
                )
                .scalars()
                .all()
            )
        assert hits == []

    async def test_reveal_requires_reason(self, client, sm, fake):
        """reveal=true 不带 reason(或过短)→ 400。"""
        uid = await self._realname_user(client, sm)
        ah = await admin_headers(sm, client, role="ops")
        resp = await client.get("/api/admin/v1/tenants?reveal=true", headers=ah)
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "common.validation"
        rows = (await client.get("/api/admin/v1/tenants", headers=ah)).json()["items"]
        assert next(t for t in rows if t["id"] == uid)["kyc_name"] == "张*"

    async def test_readonly_cannot_reveal(self, client, sm, fake):
        """readonly 带 reason 也不可 reveal(403)。"""
        await self._realname_user(client, sm)
        ro = await admin_headers(sm, client, role="readonly")
        resp = await client.get("/api/admin/v1/tenants?reveal=true&reason=客服工单核实", headers=ro)
        assert resp.status_code == 403

    async def test_reveal_sees_plaintext_and_audited_with_reason(self, client, sm, fake):
        """reveal + reason:看明文;恰好落一条敏感读审计(条数+事由)。"""
        from app.core.audit import AuditLog

        uid = await self._realname_user(client, sm)
        ah = await admin_headers(sm, client, role="ops")
        resp = await client.get(
            "/api/admin/v1/tenants?reveal=true&reason=客服工单核实身份", headers=ah
        )
        assert resp.status_code == 200, resp.text
        me = next(t for t in resp.json()["items"] if t["id"] == uid)
        assert me["kyc_status"] == "verified"
        assert me["kyc_name"] == "张三"
        async with sm() as session:
            hits = (
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.target == "tenant-realname:reveal")
                    )
                )
                .scalars()
                .all()
            )
        assert len(hits) == 1
        assert hits[0].action == "admin.GET /api/admin/v1/tenants"
        assert hits[0].detail == {"rows": 1, "reason": "客服工单核实身份"}


class TestTenantQuotaOverride:
    """配额覆盖:override 优先于 policy/env;清空恢复默认链;updated_by 落库。"""

    async def test_override_caps_disks_then_clear_restores(self, client, sm, fake):
        headers, user_id, _key = await funded_user(client, sm, "13655550002")
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

        from app.modules.adminapi.models import AdminUser

        async with sm() as session:
            admin_id = (
                await session.execute(select(AdminUser.id).where(AdminUser.username == "ops-user"))
            ).scalar_one()
        assert body["updated_by"] == admin_id and body["updated_at"]

        await create_disk(client, headers, name="d1", size_gb=50)
        blocked = await client.post(
            "/api/v1/disks", json={"name": "d2", "size_gb": 50}, headers=headers
        )
        assert blocked.status_code == 400
        assert blocked.json()["message_key"] == "disks.countQuota"
        assert blocked.json()["params"]["max"] == 1

        resp = await client.put(
            f"/api/admin/v1/tenants/{user_id}/quota",
            json={"note": "复核后恢复默认"},
            headers=ah,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["max_disks"] is None and body["effective_max_disks"] == 20
        assert body["updated_by"] is None
        got = (await client.get(f"/api/admin/v1/tenants/{user_id}/quota", headers=ah)).json()
        assert got["max_disks"] is None and got["effective_max_disks"] == 20
        await create_disk(client, headers, name="d2", size_gb=50)

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


class TestAdminInstanceEvents:
    """管理端实例事件时间线:读全角色,按时间倒序,游标分页;非管理端凭据拒绝。"""

    async def test_events_desc_and_cursor(self, client, sm, fake):
        _uh, uuid, _uid = await provision_running(client, sm, fake)
        ah = await admin_headers(sm, client, role="readonly")

        resp = await client.get(f"/api/admin/v1/instances/{uuid}/events", headers=ah)
        assert resp.status_code == 200, resp.text
        page = resp.json()
        ids = [e["id"] for e in page["items"]]
        assert len(ids) >= 2
        assert ids == sorted(ids, reverse=True)

        p1 = (await client.get(f"/api/admin/v1/instances/{uuid}/events?limit=1", headers=ah)).json()
        assert len(p1["items"]) == 1 and p1["next_cursor"]

    async def test_unknown_uuid_404(self, client, sm, fake):
        await provision_running(client, sm, fake)
        ah = await admin_headers(sm, client, role="ops")
        resp = await client.get("/api/admin/v1/instances/nope-uuid/events", headers=ah)
        assert resp.status_code == 404


class TestAdminListPagination:
    """管理端列表端点的筛选参数与 limit。"""

    async def test_orders_cursor_and_day_filter(self, client, sm, fake):
        """订单:游标走查;day=YYYY-MM-DD 只留当日单。"""
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
        bad = await client.get("/api/admin/v1/orders", params={"day": "2026-13-99"}, headers=h)
        assert bad.status_code == 400

        p1 = (await client.get("/api/admin/v1/orders", params={"limit": 2}, headers=h)).json()
        assert len(p1["items"]) == 2 and p1["next_cursor"]

    async def test_adjustments_cursor_and_filters(self, client, sm, fake):
        """调账:status/user_id 过滤 + 游标走查。"""
        from app.modules.adminapi.models import AdminUser

        _headers, _uuid, user_id = await provision_running(client, sm, fake, "13677780003")
        fin = await admin_headers(sm, client, role="finance", username="fin-page")
        async with sm() as session:
            creator = (
                await session.execute(select(AdminUser.id).where(AdminUser.username == "fin-page"))
            ).scalar_one()
            for i in range(3):
                await finance_service.create_adjustment(
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


class TestRealNameIdentityCap:
    async def test_same_id_number_bound_accounts_capped(self, client, sm, monkeypatch):
        from app.core.config import get_settings
        from app.modules.account import service as account_service
        from app.modules.account.kyc import KycResult, KycSubject, set_kyc_provider

        class _Pass:
            name = "fake"

            async def verify(self, subject: KycSubject) -> KycResult:
                return KycResult(True, "fake", identity_key=subject.identity_number)

        monkeypatch.setattr(get_settings(), "real_name_max_accounts_per_identity", 1)
        a = (await register(client, "13655550101"))["user"]["id"]
        b = (await register(client, "13655550102"))["user"]["id"]
        monkeypatch.setattr(get_settings(), "compliance_profile", "cn")
        async with sm() as session:
            await session.execute(update(User).where(User.id == a).values(phone="+8613655550101"))
            await session.execute(update(User).where(User.id == b).values(phone="+8613655550102"))
            await session.commit()
        set_kyc_provider(_Pass())
        try:
            await set_platform_setting(sm, "real_name_enabled", "true")
            async with sm() as session:
                ua = await account_service.get_user(session, a)
                await account_service.submit_kyc(session, ua, "张三", "110101199001011237")
                assert ua.kyc_identity_hmac and ua.kyc_identity_masked == "1101************37"
            async with sm() as session:
                ub = await account_service.get_user(session, b)
                with pytest.raises(AppError) as exc:
                    await account_service.submit_kyc(session, ub, "李四", "110101199001011237")
                assert exc.value.message_key == "account.realNameIdentityLimit"
            async with sm() as session:
                assert (await account_service.get_user(session, b)).kyc_status != "verified"
        finally:
            set_kyc_provider(None)
