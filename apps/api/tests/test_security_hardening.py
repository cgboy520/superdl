from httpx import AsyncClient

from tests.test_catalog import admin_headers


class TestLoginRateLimit:
    async def test_admin_login_locked_after_5_attempts(self, client: AsyncClient, sm):
        await admin_headers(sm, client)  # 创建 admin-user(消耗 1 次成功登录计数)
        for _ in range(4):
            resp = await client.post(
                "/api/admin/v1/auth/login", json={"username": "admin-user", "password": "wrong"}
            )
            assert resp.json()["code"] == "LOGIN_FAILED"
        resp = await client.post(
            "/api/admin/v1/auth/login", json={"username": "admin-user", "password": "wrong"}
        )
        assert resp.status_code == 429
        assert resp.json()["code"] == "RATE_LIMITED"
        # 正确密码也被限流拦住(锁定期内)
        resp = await client.post(
            "/api/admin/v1/auth/login", json={"username": "admin-user", "password": "pass1234"}
        )
        assert resp.status_code == 429

    async def test_user_password_login_rate_limited(self, client: AsyncClient):
        from tests.test_account_auth import register

        await register(client, "13800000077", password="secret123")
        for _ in range(5):
            await client.post(
                "/api/v1/auth/login", json={"phone": "13800000077", "password": "wrong-pass"}
            )
        resp = await client.post(
            "/api/v1/auth/login", json={"phone": "13800000077", "password": "wrong-pass"}
        )
        assert resp.status_code == 429


class TestNoDefaultBootstrapAdmin:
    def test_bootstrap_password_defaults_to_none(self, monkeypatch):
        from app.core.config import Settings

        monkeypatch.delenv("SUPERDL_BOOTSTRAP_ADMIN_PASSWORD", raising=False)
        assert Settings().bootstrap_admin_password is None
