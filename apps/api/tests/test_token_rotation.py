"""refresh 轮换与撤销:一次性消费、重放全撤、冻结即失效。"""

from httpx import AsyncClient

from tests.test_account_auth import register
from tests.test_catalog import admin_headers


async def _refresh(client: AsyncClient, refresh_token: str):
    return await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})


class TestRefreshRotation:
    async def test_rotate_then_replay_revokes_all(self, client: AsyncClient):
        data = await register(client, "13800000095")
        first = await _refresh(client, data["refresh_token"])
        assert first.status_code == 200
        pair2 = first.json()
        assert pair2["refresh_token"] != data["refresh_token"]

        # 重放旧 refresh → 401,且视为泄露:撤销该用户全部在外 token
        replay = await _refresh(client, data["refresh_token"])
        assert replay.status_code == 401
        me = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {pair2['access_token']}"}
        )
        assert me.status_code == 401
        second_refresh = await _refresh(client, pair2["refresh_token"])
        assert second_refresh.status_code == 401

    async def test_normal_chain_keeps_working(self, client: AsyncClient):
        data = await register(client, "13800000096")
        pair2 = (await _refresh(client, data["refresh_token"])).json()
        pair3 = (await _refresh(client, pair2["refresh_token"])).json()
        me = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {pair3['access_token']}"}
        )
        assert me.status_code == 200


class TestFreezeRevokesTokens:
    async def test_freeze_kills_refresh_and_access(self, client: AsyncClient, sm):
        data = await register(client, "13800000097")
        user_id = data["user"]["id"]
        ah = await admin_headers(sm, client, role="ops")
        resp = await client.post(
            f"/api/admin/v1/tenants/{user_id}/freeze", json={"reason": "违规测试"}, headers=ah
        )
        assert resp.status_code == 200, resp.text

        assert (await _refresh(client, data["refresh_token"])).status_code == 401
        me = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {data['access_token']}"}
        )
        assert me.status_code == 403  # 冻结期给明确的 USER_FROZEN 语义

        # 解冻后旧 token 仍失效(版本已推进),需重新登录
        resp = await client.post(
            f"/api/admin/v1/tenants/{user_id}/unfreeze", json={"reason": "误封"}, headers=ah
        )
        assert resp.status_code == 200, resp.text
        me = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {data['access_token']}"}
        )
        assert me.status_code == 401
