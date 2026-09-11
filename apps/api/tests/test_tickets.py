"""工单系统:创建(幂等/单号格式/上限/限流)/对话流状态机/联动通知/IDOR。"""

import re
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update

from app.core.timeutil import now_utc
from app.modules.notify.models import Notification
from app.modules.tickets.models import Ticket
from app.modules.tickets.patrol import stale_ticket_patrol
from tests.helpers import admin_headers, user_headers, user_headers_with_id


async def create_ticket(
    client: AsyncClient, headers: dict, idem: str | None = None, **overrides: object
):
    body: dict[str, object] = {
        "category": "instance",
        "subject": "实例无法开机",
        "body": "开机一直卡在 creating,请帮忙看看",
    }
    body.update(overrides)
    h = {**headers, **({"Idempotency-Key": idem} if idem else {})}
    return await client.post("/api/v1/tickets", json=body, headers=h)


async def admin_alerts(sm) -> list[Notification]:
    async with sm() as session:
        return list(
            (
                await session.execute(
                    select(Notification).where(Notification.type == "admin_alert")
                )
            ).scalars()
        )


class TestCreate:
    async def test_create_ok_and_no_format(self, client: AsyncClient, sm):
        """创建成功:status=open、首条消息落库、ticket_no 形如 T20260823-01。"""
        headers = await user_headers(client, "13700000301")
        resp = await create_ticket(client, headers)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "open"
        assert re.fullmatch(r"T\d{8}-\d{2}", body["ticket_no"])
        detail = (await client.get(f"/api/v1/tickets/{body['id']}", headers=headers)).json()
        assert [m["sender_kind"] for m in detail["messages"]] == ["user"]
        assert detail["messages"][0]["body"] == "开机一直卡在 creating,请帮忙看看"

    async def test_idempotent_replay_returns_same(self, client: AsyncClient, sm):
        """同 Idempotency-Key 重放返回同一单,不产生第二行、不耗限流配额。"""
        headers = await user_headers(client, "13700000302")
        r1 = await create_ticket(client, headers, idem="tk-1")
        r2 = await create_ticket(client, headers, idem="tk-1")
        assert r1.status_code == 201 and r2.status_code == 200
        assert r2.headers["x-idempotent-replay"] == "true"
        assert r2.json()["id"] == r1.json()["id"]
        async with sm() as session:
            count = (await session.execute(select(func.count()).select_from(Ticket))).scalar_one()
        assert count == 1

    async def test_open_limit_10_conflict(self, client: AsyncClient, sm):
        """进行中(open/pending_staff/pending_user)工单 >10 时第 11 单 409。"""
        headers, uid = await user_headers_with_id(client, "13700000303")
        # 直接播种 10 张进行中工单
        async with sm() as session:
            for i in range(10):
                session.add(
                    Ticket(
                        ticket_no=f"T20260101-{i + 1:02d}",
                        user_id=uid,
                        category="other",
                        subject=f"历史工单 {i}",
                        status=["open", "pending_staff", "pending_user"][i % 3],
                    )
                )
            await session.commit()
        resp = await create_ticket(client, headers)
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "tickets.openLimitReached"

    async def test_rate_limit_5_per_hour(self, client: AsyncClient, sm):
        """创建限流 5/h:前 5 单成功,第 6 单 429。"""
        headers = await user_headers(client, "13700000304")
        for i in range(5):
            resp = await create_ticket(client, headers, subject=f"问题 {i}")
            assert resp.status_code == 201, resp.text
        resp = await create_ticket(client, headers, subject="第 6 单")
        assert resp.status_code == 429
        assert resp.json()["code"] == "RATE_LIMITED"

    async def test_new_ticket_triggers_admin_alert(self, client: AsyncClient, sm):
        """新工单 → admin_alerts info 级告警。"""
        headers = await user_headers(client, "13700000305")
        resp = await create_ticket(client, headers)
        assert resp.status_code == 201
        ticket_no = resp.json()["ticket_no"]
        alerts = await admin_alerts(sm)
        created = [a for a in alerts if a.dedup_key == f"ticket:created:{resp.json()['id']}"]
        assert len(created) == 1
        assert created[0].severity == "info"
        assert ticket_no in created[0].content
        assert created[0].user_id is None  # 平台级告警,不属于任何租户


class TestConversation:
    async def _open_ticket(self, client: AsyncClient, phone: str) -> tuple[dict, dict]:
        headers = await user_headers(client, phone)
        resp = await create_ticket(client, headers)
        assert resp.status_code == 201, resp.text
        return headers, resp.json()

    async def test_status_transitions(self, client: AsyncClient, sm):
        """open →(客服回复)→ pending_user →(用户回复)→ pending_staff →(标记解决)→ resolved。"""
        headers, ticket = await self._open_ticket(client, "13700000311")
        tid = ticket["id"]
        ops = await admin_headers(sm, client, role="ops")

        # 客服回复 → pending_user
        resp = await client.post(
            f"/api/admin/v1/tickets/{tid}/reply", json={"body": "已为您重启,请再试"}, headers=ops
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "pending_user"
        assert [m["sender_kind"] for m in resp.json()["messages"]] == ["user", "staff"]

        # 用户回复 → pending_staff
        resp = await client.post(
            f"/api/v1/tickets/{tid}/messages", json={"body": "还是不行"}, headers=headers
        )
        assert resp.status_code == 201, resp.text
        detail = (await client.get(f"/api/v1/tickets/{tid}", headers=headers)).json()
        assert detail["status"] == "pending_staff"
        assert [m["sender_kind"] for m in detail["messages"]] == ["user", "staff", "user"]

        # 客服标记解决 → resolved
        resp = await client.post(
            f"/api/admin/v1/tickets/{tid}/status", json={"action": "resolve"}, headers=ops
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "resolved"

    async def test_terminal_not_repliable(self, client: AsyncClient, sm):
        """resolved/closed 不可再回复(用户与客服均 409);close 仅 resolved 后可。"""
        headers, ticket = await self._open_ticket(client, "13700000312")
        tid = ticket["id"]
        ops = await admin_headers(sm, client, role="ops")

        # open 状态不可直接关闭(用户端)
        resp = await client.post(f"/api/v1/tickets/{tid}/close", headers=headers)
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "tickets.stateNotClosable"

        # 标记解决后:双方回复均 409
        resp = await client.post(
            f"/api/admin/v1/tickets/{tid}/status", json={"action": "resolve"}, headers=ops
        )
        assert resp.status_code == 200
        resp = await client.post(
            f"/api/v1/tickets/{tid}/messages", json={"body": "补充说明"}, headers=headers
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "tickets.stateNotRepliable"
        resp = await client.post(
            f"/api/admin/v1/tickets/{tid}/reply", json={"body": "追加回复"}, headers=ops
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "tickets.stateNotRepliable"
        # resolved → resolved 重复标记 409
        resp = await client.post(
            f"/api/admin/v1/tickets/{tid}/status", json={"action": "resolve"}, headers=ops
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "tickets.stateNotResolvable"

        # resolved → closed:closed_at 落;此后回复/关闭/解决均 409
        resp = await client.post(f"/api/v1/tickets/{tid}/close", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "closed"
        assert resp.json()["closed_at"] is not None
        resp = await client.post(
            f"/api/v1/tickets/{tid}/messages", json={"body": "再补充"}, headers=headers
        )
        assert resp.status_code == 409
        resp = await client.post(
            f"/api/admin/v1/tickets/{tid}/status", json={"action": "close"}, headers=ops
        )
        assert resp.status_code == 409

    async def test_staff_reply_notifies_user(self, client: AsyncClient, sm):
        """客服回复 → 用户站内信(type=ticket,含工单号)。"""
        headers, ticket = await self._open_ticket(client, "13700000313")
        ops = await admin_headers(sm, client, role="ops")
        resp = await client.post(
            f"/api/admin/v1/tickets/{ticket['id']}/reply",
            json={"body": "已处理,请验证"},
            headers=ops,
        )
        assert resp.status_code == 200
        notes = (await client.get("/api/v1/notifications", headers=headers)).json()["items"]
        ticket_notes = [n for n in notes if n["type"] == "ticket"]
        assert len(ticket_notes) == 1
        assert ticket["ticket_no"] in ticket_notes[0]["content"]

    async def test_user_reply_triggers_admin_alert(self, client: AsyncClient, sm):
        """用户回复 → admin_alerts info。"""
        headers, ticket = await self._open_ticket(client, "13700000314")
        resp = await client.post(
            f"/api/v1/tickets/{ticket['id']}/messages",
            json={"body": "补充:日志见附件"},
            headers=headers,
        )
        assert resp.status_code == 201
        alerts = await admin_alerts(sm)
        reply_alerts = [
            a for a in alerts if a.dedup_key and a.dedup_key.startswith("ticket:user-reply:")
        ]
        assert len(reply_alerts) == 1
        assert reply_alerts[0].severity == "info"
        assert ticket["ticket_no"] in reply_alerts[0].content


class TestIdor:
    @pytest.mark.parametrize("probe", ["get", "message", "close"])
    async def test_other_users_ticket_invisible(self, client: AsyncClient, sm, probe: str):
        """用户 B 对用户 A 的工单:GET / POST messages / close 全部 404。"""
        ha = await user_headers(client, "13700000321")
        resp = await create_ticket(client, ha)
        assert resp.status_code == 201
        tid = resp.json()["id"]
        hb = await user_headers(client, "13700000322")

        if probe == "get":
            resp = await client.get(f"/api/v1/tickets/{tid}", headers=hb)
        elif probe == "message":
            resp = await client.post(
                f"/api/v1/tickets/{tid}/messages", json={"body": "越权追加"}, headers=hb
            )
        else:
            resp = await client.post(f"/api/v1/tickets/{tid}/close", headers=hb)
        assert resp.status_code == 404
        assert resp.json()["code"] == "NOT_FOUND"


class TestAdmin:
    async def test_filters(self, client: AsyncClient, sm):
        """status/category 精确过滤(Page 响应:items 为当前页)。"""
        headers = await user_headers(client, "13700000331")
        t1 = (await create_ticket(client, headers, category="instance")).json()
        t2 = (await create_ticket(client, headers, category="billing", subject="扣费有疑问")).json()
        ops = await admin_headers(sm, client, role="ops")
        rows = (await client.get("/api/admin/v1/tickets", headers=ops)).json()["items"]
        assert {r["id"] for r in rows} == {t1["id"], t2["id"]}
        rows = (
            await client.get("/api/admin/v1/tickets", params={"category": "billing"}, headers=ops)
        ).json()["items"]
        assert [r["id"] for r in rows] == [t2["id"]]
        # 标记 t1 解决后按 status 过滤
        resp = await client.post(
            f"/api/admin/v1/tickets/{t1['id']}/status", json={"action": "resolve"}, headers=ops
        )
        assert resp.status_code == 200
        rows = (
            await client.get("/api/admin/v1/tickets", params={"status": "resolved"}, headers=ops)
        ).json()["items"]
        assert [r["id"] for r in rows] == [t1["id"]]
        rows = (
            await client.get("/api/admin/v1/tickets", params={"status": "open"}, headers=ops)
        ).json()["items"]
        assert [r["id"] for r in rows] == [t2["id"]]

    async def test_count_endpoint(self, client: AsyncClient, sm):
        """待办计数端点:默认 pending_staff 口径,支持 status/category 过滤。"""
        headers = await user_headers(client, "13700000337")
        t1 = (await create_ticket(client, headers, category="instance")).json()
        await create_ticket(client, headers, category="billing", subject="发票咨询")
        ops = await admin_headers(sm, client, role="ops")
        # 新建工单默认 open;计数口径随 status 参数
        open_count = (
            await client.get("/api/admin/v1/tickets/count", params={"status": "open"}, headers=ops)
        ).json()["count"]
        assert open_count == 2
        billing_count = (
            await client.get(
                "/api/admin/v1/tickets/count",
                params={"status": "open", "category": "billing"},
                headers=ops,
            )
        ).json()["count"]
        assert billing_count == 1
        # 客服回复后 → pending_staff,默认口径命中
        await client.post(
            f"/api/admin/v1/tickets/{t1['id']}/reply", json={"body": "收到,处理中"}, headers=ops
        )
        default_count = (await client.get("/api/admin/v1/tickets/count", headers=ops)).json()[
            "count"
        ]
        # 回复 → pending_user,不在待客服口径
        assert default_count == 0
        # "count" 不被当 ticket_id 解析
        resp = await client.get("/api/admin/v1/tickets/count", headers=ops)
        assert resp.status_code == 200

    async def test_search_by_user_id_and_ticket_no(self, client: AsyncClient, sm):
        """user_id/ticket_no 检索。"""
        headers = await user_headers(client, "13700000335")
        t1 = (await create_ticket(client, headers, category="instance")).json()
        t2 = (await create_ticket(client, headers, category="billing", subject="账单咨询")).json()
        other = await user_headers(client, "13700000336")
        t3 = (await create_ticket(client, other, category="account", subject="注销咨询")).json()
        ops = await admin_headers(sm, client, role="ops")
        # user_id 检索
        uid = (await client.get("/api/v1/me", headers=headers)).json()["id"]
        rows = (
            await client.get("/api/admin/v1/tickets", params={"user_id": uid}, headers=ops)
        ).json()["items"]
        assert {r["id"] for r in rows} == {t1["id"], t2["id"]}
        # ticket_no 精确检索
        rows = (
            await client.get(
                "/api/admin/v1/tickets", params={"ticket_no": t3["ticket_no"]}, headers=ops
            )
        ).json()["items"]
        assert [r["id"] for r in rows] == [t3["id"]]
        # 组合检索:user_id + category
        rows = (
            await client.get(
                "/api/admin/v1/tickets",
                params={"user_id": uid, "category": "billing"},
                headers=ops,
            )
        ).json()["items"]
        assert [r["id"] for r in rows] == [t2["id"]]


class TestStaleTicketPatrol:
    """工单滞留巡检:pending_staff 超 24h → admin_alerts warning,dedup 不重复。"""

    async def _make_stale_ticket(
        self, client: AsyncClient, sm, phone: str, *, age_hours: float
    ) -> int:
        headers = await user_headers(client, phone)
        ticket = (await create_ticket(client, headers)).json()
        async with sm() as session:
            await session.execute(
                update(Ticket)
                .where(Ticket.id == ticket["id"])
                .values(
                    status="pending_staff",
                    updated_at=now_utc() - timedelta(hours=age_hours),
                )
            )
            await session.commit()
        return ticket["id"]

    async def test_stale_pending_staff_warns_and_dedups(self, client: AsyncClient, sm):
        ticket_id = await self._make_stale_ticket(client, sm, "13700000341", age_hours=25)
        assert await stale_ticket_patrol(sm) == 1
        stale = [a for a in await admin_alerts(sm) if a.dedup_key == f"ticket-stale:{ticket_id}"]
        assert len(stale) == 1
        assert stale[0].severity == "warning"
        assert stale[0].user_id is None  # 平台级告警
        # 第二次巡检不重复(dedup_key)
        assert await stale_ticket_patrol(sm) == 0

    async def test_fresh_pending_staff_not_alerted(self, client: AsyncClient, sm):
        ticket_id = await self._make_stale_ticket(client, sm, "13700000342", age_hours=1)
        assert await stale_ticket_patrol(sm) == 0
        assert [
            a for a in await admin_alerts(sm) if a.dedup_key == f"ticket-stale:{ticket_id}"
        ] == []
