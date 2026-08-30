"""refresh 轮换与撤销:一次性消费、宽限窗内并发重试放行、窗外重放全撤、冻结即失效、登出。

refresh token 只走 HttpOnly Cookie(响应体不含):测试经 cookie jar 取/覆写
(见 tests/helpers.py 的 REFRESH_COOKIE / refresh_via_cookie / logout_via_cookie)。
"""

import asyncio
from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.timeutil import now_utc
from app.modules.account.models import UsedRefreshToken
from tests.helpers import (
    REFRESH_COOKIE,
    admin_headers,
    current_refresh_token,
    issue_code,
    logout_via_cookie,
    refresh_via_cookie,
    register,
)


async def _age_used_refresh_tokens(sm: async_sessionmaker[AsyncSession]) -> None:
    """把消费记录回拨到宽限窗之外:模拟「稍后才发生的重放」(泄露场景)。"""
    async with sm() as session:
        await session.execute(
            update(UsedRefreshToken).values(used_at=now_utc() - timedelta(minutes=1))
        )
        await session.commit()


def _cookie_value(resp) -> str:
    """从 Set-Cookie 头里取 refresh cookie 值(轮换一致性断言用;响应体已不含它)。"""
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

        # 宽限窗外的重放 → 401,且视为泄露:撤销该用户全部在外 token
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
        """并发刷新(多标签页/客户端重试)同 jti 撞单:宽限窗内按正常轮换处理,
        两路拿到同一对新 token(不为同一旧 token 另开第二条有效链),不得误判泄露。

        屏障对齐起跑线:两路刷新同一时刻到达轮换闸,不依赖调度器碰巧交错。
        两个独立 client = 两个标签页各自的 jar(共享 jar 有 Set-Cookie 竞态)。"""
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
        # 重放回同一对 token:两路的 access 一致,refresh cookie 同值
        assert _cookie_value(a) == _cookie_value(b)
        assert a.json()["access_token"] == b.json()["access_token"]
        pair = a.json()
        me = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {pair['access_token']}"}
        )
        assert me.status_code == 200
        # jar 已被 Set-Cookie 推进到轮换后的值,可直接再刷
        nxt = await refresh_via_cookie(client)
        assert nxt.status_code == 200

    async def test_grace_replay_does_not_fork_chain(self, client: AsyncClient):
        """宽限窗内多次重放同一旧 token:每路都回同一对,不产生第二条长期有效链。"""
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
        # 唯一有效链继续轮换照常
        assert (await refresh_via_cookie(client, first_cookie)).status_code == 200


class TestRefreshCookie:
    """refresh token 的 HttpOnly Cookie 通道:签发/轮换/CSRF 头/登出清除。"""

    async def test_login_sets_cookie_and_cookie_refresh_rotates(self, client: AsyncClient, sm):
        await register(client, "13800000105")  # 先建号,登录路径才可验
        await issue_code(sm, "13800000105", "login")
        resp = await client.post(
            "/api/v1/auth/login", json={"phone": "13800000105", "sms_code": "123456"}
        )
        assert resp.status_code == 200, resp.text
        sc = resp.headers["set-cookie"]
        assert "superdl_refresh=" in sc
        assert "HttpOnly" in sc and "SameSite=strict" in sc
        assert "Path=/" in sc
        assert "Secure" not in sc  # dev/test 是 http,prod 才置 Secure(且改 __Host- 前缀)
        # 响应体不含 refresh_token:长期凭据不出现在 JS 可读面
        assert "refresh_token" not in resp.json()

        # cookie 路径刷新(带 CSRF 头):成功并轮换 Cookie;轮换后的新 cookie 可继续刷
        r2 = await client.post("/api/v1/auth/refresh", headers={"X-Requested-With": "fetch"})
        assert r2.status_code == 200, r2.text
        assert "superdl_refresh=" in r2.headers["set-cookie"]
        assert r2.json()["access_token"]
        assert "refresh_token" not in r2.json()
        r3 = await client.post("/api/v1/auth/refresh", headers={"X-Requested-With": "fetch"})
        assert r3.status_code == 200, r3.text

    async def test_cookie_path_requires_csrf_header(self, client: AsyncClient, sm):
        """cookie 路径缺 X-Requested-With → 403(双提交纵深);body 旁路已删除。"""
        await register(client, "13800000106")
        resp = await client.post("/api/v1/auth/refresh")  # jar 里有 cookie,无头
        assert resp.status_code == 403
        # body 旁路已删除:带 token 的 body 不再被读取(jar 有 cookie + 有头才放行)
        jar_before = current_refresh_token(client)
        ignored_body = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "not-a-jwt"},
            headers={"X-Requested-With": "fetch"},
        )
        assert ignored_body.status_code == 200  # body 被忽略,按 jar 里的真 cookie 轮换
        assert _cookie_value(ignored_body) != jar_before != "not-a-jwt"

    async def test_logout_via_cookie_clears_cookie(self, client: AsyncClient):
        await register(client, "13800000107")
        consumed = current_refresh_token(client)
        resp = await client.post("/api/v1/auth/logout", headers={"X-Requested-With": "fetch"})
        assert resp.status_code == 204
        # Set-Cookie 删除(空值 + expires 过去);该 refresh 已消费,重放 401
        assert "superdl_refresh=" in resp.headers["set-cookie"]
        assert (await refresh_via_cookie(client, consumed)).status_code == 401


class TestLogout:
    async def test_logout_revokes_refresh_token(self, client: AsyncClient, sm):
        """登出当前会话:refresh 落一次性消费位,之后再刷新一律 401。"""
        data = await register(client, "13800000101")
        consumed = current_refresh_token(client)
        resp = await logout_via_cookie(client)
        assert resp.status_code == 204
        # 重复登出幂等,仍 204(jar 已被清,显式覆写回已消费的 token)
        again = await logout_via_cookie(client, consumed)
        assert again.status_code == 204
        # 宽限窗外再用该 refresh:判重放,401 并撤销全部在外 token
        await _age_used_refresh_tokens(sm)
        assert (await refresh_via_cookie(client, consumed)).status_code == 401
        me = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {data['access_token']}"}
        )
        assert me.status_code == 401

    async def test_logout_replay_within_grace_401_without_global_revoke(
        self, client: AsyncClient, sm
    ):
        """登出消费(consumed_via=logout)的 jti 在宽限窗内重放:一律 401,
        但不 bump token_version——并发首刷已合法轮换时,在线会话不被误撤
        (旧语义:按并发重试补发新对,等于给已登出的 token 又开了一条有效链)。"""
        data = await register(client, "13800000104")
        consumed = current_refresh_token(client)
        resp = await logout_via_cookie(client)
        assert resp.status_code == 204
        replay = await refresh_via_cookie(client, consumed)
        assert replay.status_code == 401
        # 不全撤:本会话 access token 仍有效(登出本就不是即时全局失效)
        me = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {data['access_token']}"}
        )
        assert me.status_code == 200

    async def test_logout_invalid_token_still_204(self, client: AsyncClient):
        """无效/错类型 token 也回 204:不构成 token 有效性探测口。"""
        data = await register(client, "13800000102")
        garbage = await logout_via_cookie(client, "not-a-jwt")
        assert garbage.status_code == 204
        # access token 充当 refresh(类型不符)同样 204
        wrong_type = await logout_via_cookie(client, data["access_token"])
        assert wrong_type.status_code == 204

    async def test_logout_all_revokes_everything(self, client: AsyncClient, sm):
        """登出全部:token_version+1,所有会话的 access/refresh 即刻失效;账号本身可重新登录。"""
        data = await register(client, "13800000103")
        rotated = (await refresh_via_cookie(client)).json()  # 第二个会话
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

        assert (await refresh_via_cookie(client)).status_code == 401
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
