"""公告管理:列表含历史、撤回后用户端不可见、撤回幂等(重复 409)、角色门。"""

from httpx import AsyncClient

from tests.test_catalog import admin_headers
from tests.test_payment import user_headers


async def _publish(
    client: AsyncClient,
    headers: dict,
    title: str = "存储维护通知",
    content: str = "本周六 02:00-04:00 存储集群维护",
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
        await _publish(client, ops, "公告甲")
        await _publish(client, ops, "公告乙")
        resp = await client.get("/api/admin/v1/announcements", headers=ops)
        rows = resp.json()
        assert [r["title"] for r in rows] == ["公告乙", "公告甲"]  # 最新在前,含历史
        assert all(r["status"] == "published" for r in rows)
        assert all(r["reached"] == 0 for r in rows)  # 无注册用户时触达 0

    async def test_publish_idempotent_replay_no_duplicate_fanout(self, client: AsyncClient, sm):
        """HTTP 层重试(网络丢响应):同 Idempotency-Key 重放不新建公告,
        否则 dedup 域随新 id 更换,全体租户收到重复站内信。"""
        uh = await user_headers(client, "13700000402")
        ops = await admin_headers(sm, client, role="ops")
        h = {**ops, "Idempotency-Key": "ann-idem-1"}
        r1 = await client.post(
            "/api/admin/v1/announcements",
            json={"title": "存储维护通知", "content": "本周六 02:00-04:00 维护"},
            headers=h,
        )
        r2 = await client.post(
            "/api/admin/v1/announcements",
            json={"title": "存储维护通知", "content": "本周六 02:00-04:00 维护"},
            headers=h,
        )
        assert r1.status_code == 201
        assert r2.status_code == 200
        assert r2.headers["x-idempotent-replay"] == "true"
        assert r2.json()["reached"] == r1.json()["reached"]
        ids = await _announcement_ids(client, ops)
        assert len(ids) == 1  # 只落了一条公告
        notes = (await client.get("/api/v1/notifications", headers=uh)).json()["items"]
        assert len([n for n in notes if n["type"] == "announcement"]) == 1  # 用户只收到一条

    async def test_list_read_roles(self, client: AsyncClient, sm):
        for role in ("ops", "finance", "readonly"):
            headers = await admin_headers(sm, client, role=role)
            resp = await client.get("/api/admin/v1/announcements", headers=headers)
            assert resp.status_code == 200

    async def test_revoke_hides_from_user_side(self, client: AsyncClient, sm):
        uh = await user_headers(client, "13700000401")
        ops = await admin_headers(sm, client, role="ops")
        await _publish(client, ops)
        notes = (await client.get("/api/v1/notifications", headers=uh)).json()["items"]
        assert len([n for n in notes if n["type"] == "announcement"]) == 1

        (ann_id,) = await _announcement_ids(client, ops)
        resp = await client.post(
            f"/api/admin/v1/announcements/{ann_id}/revoke",
            json={"reason": "发布时间写错"},
            headers=ops,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "revoked"

        # 用户端不再展示(未读/全部两个口径都收回)
        notes = (await client.get("/api/v1/notifications", headers=uh)).json()["items"]
        assert [n for n in notes if n["type"] == "announcement"] == []
        unread = (
            await client.get("/api/v1/notifications", params={"unread": True}, headers=uh)
        ).json()["items"]
        assert [n for n in unread if n["type"] == "announcement"] == []

        # 管理端历史保留,撤回信息留痕
        row = (await client.get("/api/admin/v1/announcements", headers=ops)).json()[0]
        assert row["status"] == "revoked"
        assert row["revoked_at"] is not None
        assert row["revoke_reason"] == "发布时间写错"

    async def test_revoke_repeat_409(self, client: AsyncClient, sm):
        ops = await admin_headers(sm, client, role="ops")
        await _publish(client, ops)
        (ann_id,) = await _announcement_ids(client, ops)
        first = await client.post(
            f"/api/admin/v1/announcements/{ann_id}/revoke",
            json={"reason": "重复撤回测试"},
            headers=ops,
        )
        assert first.status_code == 200
        second = await client.post(
            f"/api/admin/v1/announcements/{ann_id}/revoke",
            json={"reason": "重复撤回测试"},
            headers=ops,
        )
        assert second.status_code == 409
        assert second.json()["message_key"] == "adminapi.announcementAlreadyRevoked"

    async def test_revoke_not_found(self, client: AsyncClient, sm):
        ops = await admin_headers(sm, client, role="ops")
        resp = await client.post(
            "/api/admin/v1/announcements/999999/revoke",
            json={"reason": "不存在"},
            headers=ops,
        )
        assert resp.status_code == 404

    async def test_write_role_gate(self, client: AsyncClient, sm):
        """finance/readonly 不可发布/撤回(403)。"""
        for role in ("finance", "readonly"):
            headers = await admin_headers(sm, client, role=role)
            resp = await client.post(
                "/api/admin/v1/announcements",
                json={"title": "越权发布", "content": "越权发布内容"},
                headers=headers,
            )
            assert resp.status_code == 403
            resp = await client.post(
                "/api/admin/v1/announcements/1/revoke",
                json={"reason": "越权撤回"},
                headers=headers,
            )
            assert resp.status_code == 403
