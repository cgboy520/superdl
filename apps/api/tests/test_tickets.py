"""Tickets: creation (idempotency / number format / cap / rate limit) / conversation state machine /
linked notifications / IDOR."""

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
        "subject": "instance will not start",
        "body": "the start hangs in creating, please take a look",
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
        """Creation succeeds: status=open, first message stored, ticket_no like T20260823-01."""
        headers = await user_headers(client, "13700000301")
        resp = await create_ticket(client, headers)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "open"
        assert re.fullmatch(r"T\d{8}-\d{2}", body["ticket_no"])
        detail = (await client.get(f"/api/v1/tickets/{body['id']}", headers=headers)).json()
        assert [m["sender_kind"] for m in detail["messages"]] == ["user"]
        assert detail["messages"][0]["body"] == "the start hangs in creating, please take a look"

    async def test_idempotent_replay_returns_same(self, client: AsyncClient, sm):
        """A replay with the same Idempotency-Key returns the same ticket, no second row, no
        rate-limit quota consumed."""
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
        """More than 10 open (open/pending_staff/pending_user) tickets: the 11th is 409."""
        headers, uid = await user_headers_with_id(client, "13700000303")
        async with sm() as session:
            for i in range(10):
                session.add(
                    Ticket(
                        ticket_no=f"T20260101-{i + 1:02d}",
                        user_id=uid,
                        category="other",
                        subject=f"old ticket {i}",
                        status=["open", "pending_staff", "pending_user"][i % 3],
                    )
                )
            await session.commit()
        resp = await create_ticket(client, headers)
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "tickets.openLimitReached"

    async def test_rate_limit_5_per_hour(self, client: AsyncClient, sm):
        """Creation rate limit 5/h: the first 5 succeed, the 6th is 429."""
        headers = await user_headers(client, "13700000304")
        for i in range(5):
            resp = await create_ticket(client, headers, subject=f"issue {i}")
            assert resp.status_code == 201, resp.text
        resp = await create_ticket(client, headers, subject="ticket 6")
        assert resp.status_code == 429
        assert resp.json()["code"] == "RATE_LIMITED"

    async def test_new_ticket_triggers_admin_alert(self, client: AsyncClient, sm):
        """New ticket → admin_alerts info-level alert."""
        headers = await user_headers(client, "13700000305")
        resp = await create_ticket(client, headers)
        assert resp.status_code == 201
        ticket_no = resp.json()["ticket_no"]
        alerts = await admin_alerts(sm)
        created = [a for a in alerts if a.dedup_key == f"ticket:created:{resp.json()['id']}"]
        assert len(created) == 1
        assert created[0].severity == "info"
        assert ticket_no in created[0].content
        assert created[0].user_id is None


class TestConversation:
    async def _open_ticket(self, client: AsyncClient, phone: str) -> tuple[dict, dict]:
        headers = await user_headers(client, phone)
        resp = await create_ticket(client, headers)
        assert resp.status_code == 201, resp.text
        return headers, resp.json()

    async def test_status_transitions(self, client: AsyncClient, sm):
        """open →(staff reply)→ pending_user →(user reply)→ pending_staff →(mark resolved)→
        resolved."""
        headers, ticket = await self._open_ticket(client, "13700000311")
        tid = ticket["id"]
        ops = await admin_headers(sm, client, role="ops")

        resp = await client.post(
            f"/api/admin/v1/tickets/{tid}/reply",
            json={"body": "restarted it for you, try again"},
            headers=ops,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "pending_user"
        assert [m["sender_kind"] for m in resp.json()["messages"]] == ["user", "staff"]

        resp = await client.post(
            f"/api/v1/tickets/{tid}/messages", json={"body": "still not working"}, headers=headers
        )
        assert resp.status_code == 201, resp.text
        detail = (await client.get(f"/api/v1/tickets/{tid}", headers=headers)).json()
        assert detail["status"] == "pending_staff"
        assert [m["sender_kind"] for m in detail["messages"]] == ["user", "staff", "user"]

        resp = await client.post(
            f"/api/admin/v1/tickets/{tid}/status", json={"action": "resolve"}, headers=ops
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "resolved"

    async def test_terminal_not_repliable(self, client: AsyncClient, sm):
        """resolved/closed accept no replies (user and staff both 409); close only after
        resolved."""
        headers, ticket = await self._open_ticket(client, "13700000312")
        tid = ticket["id"]
        ops = await admin_headers(sm, client, role="ops")

        resp = await client.post(f"/api/v1/tickets/{tid}/close", headers=headers)
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "tickets.stateNotClosable"

        resp = await client.post(
            f"/api/admin/v1/tickets/{tid}/status", json={"action": "resolve"}, headers=ops
        )
        assert resp.status_code == 200
        resp = await client.post(
            f"/api/v1/tickets/{tid}/messages", json={"body": "additional details"}, headers=headers
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "tickets.stateNotRepliable"
        resp = await client.post(
            f"/api/admin/v1/tickets/{tid}/reply", json={"body": "follow-up reply"}, headers=ops
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "tickets.stateNotRepliable"
        resp = await client.post(
            f"/api/admin/v1/tickets/{tid}/status", json={"action": "resolve"}, headers=ops
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "tickets.stateNotResolvable"

        resp = await client.post(f"/api/v1/tickets/{tid}/close", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "closed"
        assert resp.json()["closed_at"] is not None
        resp = await client.post(
            f"/api/v1/tickets/{tid}/messages", json={"body": "one more note"}, headers=headers
        )
        assert resp.status_code == 409
        resp = await client.post(
            f"/api/admin/v1/tickets/{tid}/status", json={"action": "close"}, headers=ops
        )
        assert resp.status_code == 409

    async def test_staff_reply_notifies_user(self, client: AsyncClient, sm):
        """Staff reply → user in-app notification (type=ticket, with the ticket number)."""
        headers, ticket = await self._open_ticket(client, "13700000313")
        ops = await admin_headers(sm, client, role="ops")
        resp = await client.post(
            f"/api/admin/v1/tickets/{ticket['id']}/reply",
            json={"body": "handled, please verify"},
            headers=ops,
        )
        assert resp.status_code == 200
        notes = (await client.get("/api/v1/notifications", headers=headers)).json()["items"]
        ticket_notes = [n for n in notes if n["type"] == "ticket"]
        assert len(ticket_notes) == 1
        assert ticket["ticket_no"] in ticket_notes[0]["content"]

    async def test_user_reply_triggers_admin_alert(self, client: AsyncClient, sm):
        """User reply → admin_alerts info."""
        headers, ticket = await self._open_ticket(client, "13700000314")
        resp = await client.post(
            f"/api/v1/tickets/{ticket['id']}/messages",
            json={"body": "addendum: see the attached log"},
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
        """User B on user A's ticket: GET / POST messages / close are all 404."""
        ha = await user_headers(client, "13700000321")
        resp = await create_ticket(client, ha)
        assert resp.status_code == 201
        tid = resp.json()["id"]
        hb = await user_headers(client, "13700000322")

        if probe == "get":
            resp = await client.get(f"/api/v1/tickets/{tid}", headers=hb)
        elif probe == "message":
            resp = await client.post(
                f"/api/v1/tickets/{tid}/messages", json={"body": "unauthorised append"}, headers=hb
            )
        else:
            resp = await client.post(f"/api/v1/tickets/{tid}/close", headers=hb)
        assert resp.status_code == 404
        assert resp.json()["code"] == "NOT_FOUND"


class TestAdmin:
    async def test_filters(self, client: AsyncClient, sm):
        """status/category exact filters (Page response: items is the current page)."""
        headers = await user_headers(client, "13700000331")
        t1 = (await create_ticket(client, headers, category="instance")).json()
        t2 = (
            await create_ticket(
                client, headers, category="billing", subject="question about a charge"
            )
        ).json()
        ops = await admin_headers(sm, client, role="ops")
        rows = (await client.get("/api/admin/v1/tickets", headers=ops)).json()["items"]
        assert {r["id"] for r in rows} == {t1["id"], t2["id"]}
        rows = (
            await client.get("/api/admin/v1/tickets", params={"category": "billing"}, headers=ops)
        ).json()["items"]
        assert [r["id"] for r in rows] == [t2["id"]]
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
        """Pending count endpoint: default pending_staff, supports status/category filters."""
        headers = await user_headers(client, "13700000337")
        t1 = (await create_ticket(client, headers, category="instance")).json()
        await create_ticket(client, headers, category="billing", subject="invoice question")
        ops = await admin_headers(sm, client, role="ops")
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
        await client.post(
            f"/api/admin/v1/tickets/{t1['id']}/reply",
            json={"body": "received, working on it"},
            headers=ops,
        )
        default_count = (await client.get("/api/admin/v1/tickets/count", headers=ops)).json()[
            "count"
        ]
        assert default_count == 0
        resp = await client.get("/api/admin/v1/tickets/count", headers=ops)
        assert resp.status_code == 200

    async def test_search_by_user_id_and_ticket_no(self, client: AsyncClient, sm):
        """user_id/ticket_no search."""
        headers = await user_headers(client, "13700000335")
        t1 = (await create_ticket(client, headers, category="instance")).json()
        t2 = (
            await create_ticket(client, headers, category="billing", subject="bill question")
        ).json()
        other = await user_headers(client, "13700000336")
        t3 = (
            await create_ticket(client, other, category="account", subject="deletion question")
        ).json()
        ops = await admin_headers(sm, client, role="ops")
        uid = (await client.get("/api/v1/me", headers=headers)).json()["id"]
        rows = (
            await client.get("/api/admin/v1/tickets", params={"user_id": uid}, headers=ops)
        ).json()["items"]
        assert {r["id"] for r in rows} == {t1["id"], t2["id"]}
        rows = (
            await client.get(
                "/api/admin/v1/tickets", params={"ticket_no": t3["ticket_no"]}, headers=ops
            )
        ).json()["items"]
        assert [r["id"] for r in rows] == [t3["id"]]
        rows = (
            await client.get(
                "/api/admin/v1/tickets",
                params={"user_id": uid, "category": "billing"},
                headers=ops,
            )
        ).json()["items"]
        assert [r["id"] for r in rows] == [t2["id"]]


class TestStaleTicketPatrol:
    """Stale ticket patrol: pending_staff for more than 24 h → admin_alerts warning, dedup prevents
    repeats."""

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
        assert stale[0].user_id is None
        assert await stale_ticket_patrol(sm) == 0

    async def test_fresh_pending_staff_not_alerted(self, client: AsyncClient, sm):
        ticket_id = await self._make_stale_ticket(client, sm, "13700000342", age_hours=1)
        assert await stale_ticket_patrol(sm) == 0
        assert [
            a for a in await admin_alerts(sm) if a.dedup_key == f"ticket-stale:{ticket_id}"
        ] == []


class TestMessageCap:
    async def test_replies_per_ticket_capped(self, client: AsyncClient, sm, monkeypatch):
        from app.modules.tickets import service as tickets_service

        monkeypatch.setattr(tickets_service, "MAX_MESSAGES_PER_TICKET", 1)
        headers = await user_headers(client, "13700000399")
        resp = await create_ticket(client, headers)
        assert resp.status_code == 201, resp.text
        tid = resp.json()["id"]
        ops = await admin_headers(sm, client, role="ops")
        assert (
            await client.post(
                f"/api/admin/v1/tickets/{tid}/reply", json={"body": "handled"}, headers=ops
            )
        ).status_code == 200
        resp = await client.post(
            f"/api/v1/tickets/{tid}/messages", json={"body": "follow-up"}, headers=headers
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["message_key"] == "tickets.messageLimitReached"
