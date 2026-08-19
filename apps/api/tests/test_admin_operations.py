"""管理端运营刚需:outbox 死信重放/忽略、公告群发。"""

from httpx import AsyncClient
from sqlalchemy import select

from app.core.outbox import OutboxTask
from app.modules.notify.models import Notification
from tests.test_account_auth import register
from tests.test_catalog import admin_headers


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
        from decimal import Decimal

        from app.modules.billing.models import BalanceLedger

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
