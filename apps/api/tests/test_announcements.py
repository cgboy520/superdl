"""Announcement list, publish idempotency and withdrawal contracts."""

from httpx import AsyncClient

from tests.helpers import admin_headers, user_headers


async def _publish(
    client: AsyncClient,
    headers: dict,
    title: str = "Storage maintenance notice",
    content: str = "Storage cluster maintenance this Saturday 02:00-04:00",
) -> None:
    resp = await client.post(
        "/api/admin/v1/announcements",
        json={"title": title, "content": content},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text


async def _announcement_ids(client: AsyncClient, headers: dict) -> list[int]:
    resp = await client.get("/api/admin/v1/announcements", headers=headers)
    assert resp.status_code == 200, resp.text
    return [r["id"] for r in resp.json()]


class TestAnnouncementAdmin:
    async def test_list_contains_history(self, client: AsyncClient, sm):
        ops = await admin_headers(sm, client, role="ops")
        await _publish(client, ops, "announcement A")
        await _publish(client, ops, "announcement B")
        resp = await client.get("/api/admin/v1/announcements", headers=ops)
        rows = resp.json()
        assert [r["title"] for r in rows] == ["announcement B", "announcement A"]
        assert all(r["status"] == "published" for r in rows)
        assert all(r["reached"] == 0 for r in rows)

    async def test_publish_idempotent_replay_no_duplicate_fanout(self, client: AsyncClient, sm):
        """A replay with the same Idempotency-Key creates no announcement."""
        uh = await user_headers(client, "13700000402")
        ops = await admin_headers(sm, client, role="ops")
        h = {**ops, "Idempotency-Key": "ann-idem-1"}
        r1 = await client.post(
            "/api/admin/v1/announcements",
            json={
                "title": "Storage maintenance notice",
                "content": "maintenance this Saturday 02:00-04:00",
            },
            headers=h,
        )
        r2 = await client.post(
            "/api/admin/v1/announcements",
            json={
                "title": "Storage maintenance notice",
                "content": "maintenance this Saturday 02:00-04:00",
            },
            headers=h,
        )
        assert r1.status_code == 201
        assert r2.status_code == 200
        assert r2.headers["x-idempotent-replay"] == "true"
        assert r2.json()["reached"] == r1.json()["reached"]
        ids = await _announcement_ids(client, ops)
        assert len(ids) == 1
        notes = (await client.get("/api/v1/notifications", headers=uh)).json()["items"]
        assert len([n for n in notes if n["type"] == "announcement"]) == 1

    async def test_revoke_hides_from_user_side(self, client: AsyncClient, sm):
        uh = await user_headers(client, "13700000401")
        ops = await admin_headers(sm, client, role="ops")
        await _publish(client, ops)
        notes = (await client.get("/api/v1/notifications", headers=uh)).json()["items"]
        assert len([n for n in notes if n["type"] == "announcement"]) == 1

        (ann_id,) = await _announcement_ids(client, ops)
        resp = await client.post(
            f"/api/admin/v1/announcements/{ann_id}/revoke",
            json={"reason": "wrong publication time"},
            headers=ops,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "revoked"

        notes = (await client.get("/api/v1/notifications", headers=uh)).json()["items"]
        assert [n for n in notes if n["type"] == "announcement"] == []
        unread = (
            await client.get("/api/v1/notifications", params={"unread": True}, headers=uh)
        ).json()["items"]
        assert [n for n in unread if n["type"] == "announcement"] == []

        row = (await client.get("/api/admin/v1/announcements", headers=ops)).json()[0]
        assert row["status"] == "revoked"
        assert row["revoked_at"] is not None
        assert row["revoke_reason"] == "wrong publication time"

    async def test_revoke_repeat_409(self, client: AsyncClient, sm):
        ops = await admin_headers(sm, client, role="ops")
        await _publish(client, ops)
        (ann_id,) = await _announcement_ids(client, ops)
        first = await client.post(
            f"/api/admin/v1/announcements/{ann_id}/revoke",
            json={"reason": "repeated withdrawal test"},
            headers=ops,
        )
        assert first.status_code == 200
        second = await client.post(
            f"/api/admin/v1/announcements/{ann_id}/revoke",
            json={"reason": "repeated withdrawal test"},
            headers=ops,
        )
        assert second.status_code == 409
        assert second.json()["message_key"] == "adminapi.announcementAlreadyRevoked"
