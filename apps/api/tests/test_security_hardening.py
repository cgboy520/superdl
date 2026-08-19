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


class TestSmsCodeBruteForce:
    async def test_code_burned_after_max_attempts(self, client: AsyncClient, sm):
        """同一条验证码失败 5 次后作废:正确码也不再放行(计次持久化于 DB)。"""
        import pytest

        from app.core.errors import AppError
        from app.modules.account import service as account_service

        phone = "13800000088"
        resp = await client.post(
            "/api/v1/auth/sms-code", json={"phone": phone, "purpose": "register"}
        )
        assert resp.status_code == 204
        for _ in range(5):
            async with sm() as session:
                with pytest.raises(AppError):
                    await account_service._consume_sms_code(session, phone, "000000", "register")
        async with sm() as session:
            with pytest.raises(AppError):  # mock 固定码 123456 本是正确码
                await account_service._consume_sms_code(session, phone, "123456", "register")

    async def test_sms_login_rate_limited(self, client: AsyncClient):
        """验证码登录路径与密码路径同限流(否则可穷举 6 位码)。"""
        from tests.test_account_auth import register

        phone = "13800000089"
        await register(client, phone)
        for _ in range(5):
            resp = await client.post(
                "/api/v1/auth/login", json={"phone": phone, "sms_code": "000000"}
            )
            assert resp.json()["code"] == "LOGIN_FAILED"
        resp = await client.post("/api/v1/auth/login", json={"phone": phone, "sms_code": "000000"})
        assert resp.status_code == 429
        assert resp.json()["code"] == "RATE_LIMITED"

    async def test_sms_send_ip_rate_limited(self, client: AsyncClient):
        """同一 IP 高频对不同号码发码 → 429(防短信轰炸/成本攻击)。"""
        for i in range(20):
            resp = await client.post(
                "/api/v1/auth/sms-code",
                json={"phone": f"138000001{i:02d}", "purpose": "register"},
            )
            assert resp.status_code == 204
        resp = await client.post(
            "/api/v1/auth/sms-code", json={"phone": "13800000199", "purpose": "register"}
        )
        assert resp.status_code == 429
