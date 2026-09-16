"""HttpOnly Cookie 中 refresh token 的轮换、重试宽限、撤销与登出契约。"""

import asyncio
from datetime import timedelta

from httpx import AsyncClient, Response
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.timeutil import now_utc
from app.modules.account.models import UsedRefreshToken
from tests.helpers import (
    REFRESH_COOKIE,
    admin_headers,
    as_handle,
    current_refresh_token,
    issue_code,
    refresh_via_cookie,
    register,
)


async def logout_via_cookie(client: AsyncClient, token: str | None = None) -> Response:
    """cookie 通道登出;token 给定时先覆写 jar。"""
    if token is not None:
        client.cookies.set(REFRESH_COOKIE, token, path="/")
    return await client.post("/api/v1/auth/logout", headers={"X-Requested-With": "fetch"})


async def _age_used_refresh_tokens(sm: async_sessionmaker[AsyncSession]) -> None:
    """将已消费 refresh 记录的 used_at 设为一分钟前。"""
    async with sm() as session:
        await session.execute(
            update(UsedRefreshToken).values(used_at=now_utc() - timedelta(minutes=1))
        )
        await session.commit()


def _cookie_value(resp) -> str:
    """从 Set-Cookie 头里取 refresh cookie 值。"""
    sc = resp.headers["set-cookie"]
    assert f"{REFRESH_COOKIE}=" in sc
    return sc.split(f"{REFRESH_COOKIE}=", 1)[1].split(";", 1)[0]


class TestRefreshRotation:
    async def test_rotate_then_replay_revokes_all(self, client: AsyncClient, sm):
        await register(client, "13800000095")
        original = current_refresh_token(client)
        first = await refresh_via_cookie(client, original)
        assert first.status_code == 200
        rotated = _cookie_value(first)
        assert rotated != original
        pair2 = first.json()

        await _age_used_refresh_tokens(sm)
        replay = await refresh_via_cookie(client, original)
        assert replay.status_code == 401
        me = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {pair2['access_token']}"}
        )
        assert me.status_code == 401
        second_refresh = await refresh_via_cookie(client, rotated)
        assert second_refresh.status_code == 401

    async def test_concurrent_refresh_treated_as_retry(self, client: AsyncClient):
        """宽限窗内并发刷新同一 jti 返回同一对新令牌。"""
        from httpx import ASGITransport

        from app.main import create_app

        await register(client, "13800000098")
        original = current_refresh_token(client)
        other = AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test")
        other.cookies.set(REFRESH_COOKIE, original, path="/")
        gate = asyncio.Barrier(3)

        async def refresh(c: AsyncClient):
            await gate.wait()
            return await refresh_via_cookie(c)

        try:
            a, b, _ = await asyncio.gather(refresh(client), refresh(other), gate.wait())
        finally:
            await other.aclose()
        assert a.status_code == 200, a.text
        assert b.status_code == 200, b.text
        assert _cookie_value(a) == _cookie_value(b)
        assert a.json()["access_token"] == b.json()["access_token"]
        pair = a.json()
        me = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {pair['access_token']}"}
        )
        assert me.status_code == 200
        nxt = await refresh_via_cookie(client)
        assert nxt.status_code == 200

    async def test_grace_replay_does_not_fork_chain(self, client: AsyncClient):
        """宽限窗内多次重放同一旧 token:每路都回同一对。"""
        await register(client, "13800000099")
        original = current_refresh_token(client)
        first = await refresh_via_cookie(client, original)
        first_access = first.json()["access_token"]
        first_cookie = _cookie_value(first)
        for _ in range(3):
            replay = await refresh_via_cookie(client, original)
            assert replay.status_code == 200
            assert replay.json()["access_token"] == first_access
            assert _cookie_value(replay) == first_cookie
        assert (await refresh_via_cookie(client, first_cookie)).status_code == 200


class TestRefreshCookie:
    """refresh token 的 HttpOnly Cookie 通道:签发/轮换/CSRF 头/登出清除。"""

    async def test_login_sets_cookie_and_cookie_refresh_rotates(self, client: AsyncClient, sm):
        await register(client, "13800000105")
        await issue_code(sm, "13800000105", "login")
        resp = await client.post(
            "/api/v1/auth/login", json={"handle": as_handle("13800000105"), "code": "123456"}
        )
        assert resp.status_code == 200, resp.text
        sc = resp.headers["set-cookie"]
        assert "superdl_refresh=" in sc
        assert "HttpOnly" in sc and "SameSite=strict" in sc
        assert "Path=/" in sc
        assert "Secure" not in sc
        assert "refresh_token" not in resp.json()

        r2 = await client.post("/api/v1/auth/refresh", headers={"X-Requested-With": "fetch"})
        assert r2.status_code == 200, r2.text
        assert "superdl_refresh=" in r2.headers["set-cookie"]
        assert r2.json()["access_token"]
        assert "refresh_token" not in r2.json()
        r3 = await client.post("/api/v1/auth/refresh", headers={"X-Requested-With": "fetch"})
        assert r3.status_code == 200, r3.text

    async def test_cookie_path_requires_csrf_header(self, client: AsyncClient, sm):
        """cookie 路径缺 X-Requested-With → 403;body 不构成刷新旁路。"""
        await register(client, "13800000106")
        resp = await client.post("/api/v1/auth/refresh")
        assert resp.status_code == 403
        jar_before = current_refresh_token(client)
        ignored_body = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "not-a-jwt"},
            headers={"X-Requested-With": "fetch"},
        )
        assert ignored_body.status_code == 200
        assert _cookie_value(ignored_body) != jar_before != "not-a-jwt"

    async def test_logout_via_cookie_clears_cookie(self, client: AsyncClient):
        await register(client, "13800000107")
        consumed = current_refresh_token(client)
        resp = await client.post("/api/v1/auth/logout", headers={"X-Requested-With": "fetch"})
        assert resp.status_code == 204
        assert "superdl_refresh=" in resp.headers["set-cookie"]
        assert (await refresh_via_cookie(client, consumed)).status_code == 401


class TestLogout:
    async def test_logout_revokes_refresh_token(self, client: AsyncClient, sm):
        """登出当前会话:refresh 落一次性消费位,之后再刷新一律 401。"""
        data = await register(client, "13800000101")
        consumed = current_refresh_token(client)
        resp = await logout_via_cookie(client)
        assert resp.status_code == 204
        again = await logout_via_cookie(client, consumed)
        assert again.status_code == 204
        await _age_used_refresh_tokens(sm)
        assert (await refresh_via_cookie(client, consumed)).status_code == 401
        me = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {data['access_token']}"}
        )
        assert me.status_code == 401

    async def test_logout_replay_within_grace_401_without_global_revoke(
        self, client: AsyncClient, sm
    ):
        """登出消费(consumed_via=logout)的 jti 在宽限窗内重放:401,不 bump token_version。"""
        data = await register(client, "13800000104")
        consumed = current_refresh_token(client)
        resp = await logout_via_cookie(client)
        assert resp.status_code == 204
        replay = await refresh_via_cookie(client, consumed)
        assert replay.status_code == 401
        me = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {data['access_token']}"}
        )
        assert me.status_code == 200

    async def test_logout_invalid_token_still_204(self, client: AsyncClient):
        """无效/错类型 token 也回 204。"""
        data = await register(client, "13800000102")
        garbage = await logout_via_cookie(client, "not-a-jwt")
        assert garbage.status_code == 204
        wrong_type = await logout_via_cookie(client, data["access_token"])
        assert wrong_type.status_code == 204

    async def test_logout_all_revokes_everything(self, client: AsyncClient, sm):
        """登出全部:token_version+1,所有会话的 access/refresh 即刻失效;账号本身可重新登录。"""
        data = await register(client, "13800000103")
        rotated = (await refresh_via_cookie(client)).json()
        rotated_cookie = current_refresh_token(client)

        resp = await client.post(
            "/api/v1/auth/logout-all",
            headers={"Authorization": f"Bearer {data['access_token']}"},
        )
        assert resp.status_code == 204

        for pair in (data, rotated):
            me = await client.get(
                "/api/v1/me", headers={"Authorization": f"Bearer {pair['access_token']}"}
            )
            assert me.status_code == 401
        assert (await refresh_via_cookie(client, rotated_cookie)).status_code == 401
        await issue_code(sm, "13800000103", "login")
        relogin = await client.post(
            "/api/v1/auth/login", json={"handle": as_handle("13800000103"), "code": "123456"}
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

        assert (await refresh_via_cookie(client)).status_code == 401
        me = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {data['access_token']}"}
        )
        assert me.status_code == 403
        assert me.json()["message_key"] == "account.userFrozen"

        resp = await client.post(
            f"/api/admin/v1/tenants/{user_id}/unfreeze", json={"reason": "误封"}, headers=ah
        )
        assert resp.status_code == 200, resp.text
        me = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {data['access_token']}"}
        )
        assert me.status_code == 401
