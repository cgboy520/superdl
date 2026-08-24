"""refresh 轮换与撤销:一次性消费、宽限窗内并发重试放行、窗外重放全撤、冻结即失效、登出。"""

import asyncio
from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.timeutil import now_utc
from app.modules.account.models import UsedRefreshToken
from tests.test_account_auth import issue_code, register
from tests.test_catalog import admin_headers


async def _refresh(client: AsyncClient, refresh_token: str):
    return await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})


async def _age_used_refresh_tokens(sm: async_sessionmaker[AsyncSession]) -> None:
    """把消费记录回拨到宽限窗之外:模拟「稍后才发生的重放」(泄露场景)。"""
    async with sm() as session:
        await session.execute(
            update(UsedRefreshToken).values(used_at=now_utc() - timedelta(minutes=1))
        )
        await session.commit()


class TestRefreshRotation:
    async def test_rotate_then_replay_revokes_all(self, client: AsyncClient, sm):
        data = await register(client, "13800000095")
        first = await _refresh(client, data["refresh_token"])
        assert first.status_code == 200
        pair2 = first.json()
        assert pair2["refresh_token"] != data["refresh_token"]

        # 宽限窗外的重放 → 401,且视为泄露:撤销该用户全部在外 token
        await _age_used_refresh_tokens(sm)
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

    async def test_concurrent_refresh_treated_as_retry(self, client: AsyncClient):
        """并发刷新(多标签页/客户端重试)同 jti 撞单:宽限窗内按正常轮换处理,
        两路拿到同一对新 token(不为同一旧 token 另开第二条有效链),不得误判泄露。

        屏障对齐起跑线:两路刷新同一时刻到达轮换闸,不依赖调度器碰巧交错。"""
        data = await register(client, "13800000098")
        gate = asyncio.Barrier(3)

        async def refresh():
            await gate.wait()
            return await _refresh(client, data["refresh_token"])

        a, b, _ = await asyncio.gather(refresh(), refresh(), gate.wait())
        assert a.status_code == 200, a.text
        assert b.status_code == 200, b.text
        # 重放回同一对 token:两路的 access/refresh 完全一致
        assert a.json()["refresh_token"] == b.json()["refresh_token"]
        assert a.json()["access_token"] == b.json()["access_token"]
        pair = a.json()
        me = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {pair['access_token']}"}
        )
        assert me.status_code == 200
        nxt = await _refresh(client, pair["refresh_token"])
        assert nxt.status_code == 200

    async def test_grace_replay_does_not_fork_chain(self, client: AsyncClient):
        """宽限窗内多次重放同一旧 token:每路都回同一对,不产生第二条长期有效链。"""
        data = await register(client, "13800000099")
        first = (await _refresh(client, data["refresh_token"])).json()
        for _ in range(3):
            replay = await _refresh(client, data["refresh_token"])
            assert replay.status_code == 200
            assert replay.json()["refresh_token"] == first["refresh_token"]
            assert replay.json()["access_token"] == first["access_token"]
        # 唯一有效链继续轮换照常
        assert (await _refresh(client, first["refresh_token"])).status_code == 200


class TestLogout:
    async def test_logout_revokes_refresh_token(self, client: AsyncClient, sm):
        """登出当前会话:refresh 落一次性消费位,之后再刷新一律 401。"""
        data = await register(client, "13800000101")
        resp = await client.post(
            "/api/v1/auth/logout", json={"refresh_token": data["refresh_token"]}
        )
        assert resp.status_code == 204
        # 重复登出幂等,仍 204
        again = await client.post(
            "/api/v1/auth/logout", json={"refresh_token": data["refresh_token"]}
        )
        assert again.status_code == 204
        # 宽限窗外再用该 refresh:判重放,401 并撤销全部在外 token
        await _age_used_refresh_tokens(sm)
        assert (await _refresh(client, data["refresh_token"])).status_code == 401
        me = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {data['access_token']}"}
        )
        assert me.status_code == 401

    async def test_logout_invalid_token_still_204(self, client: AsyncClient):
        """无效/错类型 token 也回 204:不构成 token 有效性探测口。"""
        data = await register(client, "13800000102")
        garbage = await client.post("/api/v1/auth/logout", json={"refresh_token": "not-a-jwt"})
        assert garbage.status_code == 204
        # access token 充当 refresh(类型不符)同样 204
        wrong_type = await client.post(
            "/api/v1/auth/logout", json={"refresh_token": data["access_token"]}
        )
        assert wrong_type.status_code == 204

    async def test_logout_all_revokes_everything(self, client: AsyncClient, sm):
        """登出全部:token_version+1,所有会话的 access/refresh 即刻失效;账号本身可重新登录。"""
        data = await register(client, "13800000103")
        other = (await _refresh(client, data["refresh_token"])).json()  # 第二个会话

        resp = await client.post(
            "/api/v1/auth/logout-all",
            headers={"Authorization": f"Bearer {data['access_token']}"},
        )
        assert resp.status_code == 204

        for pair in (data, other):
            me = await client.get(
                "/api/v1/me", headers={"Authorization": f"Bearer {pair['access_token']}"}
            )
            assert me.status_code == 401
        assert (await _refresh(client, other["refresh_token"])).status_code == 401
        # 登出全部不锁账号:凭验证码重新登录照常
        await issue_code(sm, "13800000103", "login")
        relogin = await client.post(
            "/api/v1/auth/login", json={"phone": "13800000103", "sms_code": "123456"}
        )
        assert relogin.status_code == 200, relogin.text


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
        assert me.json()["message_key"] == "account.userFrozen"

        # 解冻后旧 token 仍失效(版本已推进),需重新登录
        resp = await client.post(
            f"/api/admin/v1/tenants/{user_id}/unfreeze", json={"reason": "误封"}, headers=ah
        )
        assert resp.status_code == 200, resp.text
        me = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {data['access_token']}"}
        )
        assert me.status_code == 401
