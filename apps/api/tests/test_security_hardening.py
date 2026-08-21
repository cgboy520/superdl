import base64

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

    async def test_counter_survives_business_rollback(self, client: AsyncClient, sm):
        """限流计数走独立事务:业务事务回滚不能把这次尝试抹掉(否则可无限重试)。"""
        from sqlalchemy import select

        from app.core.ratelimit import RateLimitCounter

        for _ in range(3):
            # 手机号不存在 → LOGIN_FAILED,业务 session 全程未 commit
            await client.post("/api/v1/auth/login", json={"phone": "13800000078", "password": "x"})
        async with sm() as session:
            rows = (
                (
                    await session.execute(
                        select(RateLimitCounter).where(
                            RateLimitCounter.key.like("user-login:%13800000078")
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert [r.hits for r in rows] == [3]


class TestNoDefaultBootstrapAdmin:
    def test_bootstrap_password_defaults_to_none(self, monkeypatch):
        from app.core.config import Settings

        monkeypatch.delenv("SUPERDL_BOOTSTRAP_ADMIN_PASSWORD", raising=False)
        # _env_file=None:只验代码默认值,不受本地 dev .env 影响
        assert Settings(_env_file=None).bootstrap_admin_password is None  # type: ignore[call-arg]


class TestProdConfigValidation:
    def test_prod_rejects_dev_defaults(self):
        """environment=prod + 任一开发默认值 → 启动即拒。"""
        import pytest
        from pydantic import ValidationError

        from app.core.config import Settings

        with pytest.raises(ValidationError) as ei:
            Settings(
                _env_file=None,  # pyright: ignore[reportCallIssue] - 运行时参数,stub 未暴露
                environment="prod",
                database_url="postgresql+asyncpg://superdl:superdl@localhost:5432/superdl",
            )
        msg = str(ei.value)
        for keyword in ("jwt_secret", "sms_provider", "k8s_backend", "payment_mock"):
            assert keyword in msg

    def test_prod_accepts_complete_config(self):
        from app.core.config import Settings

        s = Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue] - 运行时参数,stub 未暴露
            environment="prod",
            jwt_secret="x" * 40,
            sms_provider="aliyun",
            sms_access_key_id="ak",
            sms_access_key_secret="sk",
            sms_sign_name="SuperDL",
            sms_template_verify="SMS_1",
            sms_template_notice="SMS_2",
            k8s_backend="real",
            payment_mock=False,
            database_url="postgresql+asyncpg://svc:strongpass@pg.internal:5432/superdl",
            cors_origins=["https://console.superdl.cn"],
            ssh_host="ssh1.superdl.cn",
            jupyter_domain_suffix="app.superdl.cn",
            public_base_url="https://api.superdl.cn",
            prometheus_url="http://kube-prometheus-stack-prometheus.monitoring.svc:9090",
            alertmanager_token="token",
            metrics_token="mtoken",
            config_encryption_key=base64.urlsafe_b64encode(b"k" * 32).decode(),
        )
        assert s.environment == "prod"

    def test_prod_rejects_localhost_prometheus(self):
        """prometheus_url 保持本地默认会静默失效(计费无恙但面板/对账全空),prod 必拒。"""
        import pytest as _pytest

        from app.core.config import Settings

        with _pytest.raises(ValueError, match="prometheus_url"):
            Settings(
                _env_file=None,  # pyright: ignore[reportCallIssue]
                environment="prod",
                jwt_secret="x" * 40,
                sms_provider="aliyun",
                sms_access_key_id="ak",
                sms_access_key_secret="sk",
                sms_sign_name="SuperDL",
                sms_template_verify="SMS_1",
                sms_template_notice="SMS_2",
                k8s_backend="real",
                payment_mock=False,
                database_url="postgresql+asyncpg://svc:strongpass@pg.internal:5432/superdl",
                cors_origins=["https://console.superdl.cn"],
                ssh_host="ssh1.superdl.cn",
                jupyter_domain_suffix="app.superdl.cn",
                public_base_url="https://api.superdl.cn",
                alertmanager_token="token",
                metrics_token="mtoken",
                config_encryption_key=base64.urlsafe_b64encode(b"k" * 32).decode(),
            )


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


class TestSecurityHeaders:
    async def test_headers_on_api_responses(self, client: AsyncClient):
        resp = await client.get("/healthz")
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["x-frame-options"] == "DENY"
        assert "default-src 'none'" in resp.headers["content-security-policy"]
        assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"

    async def test_docs_exempt_from_csp(self, client: AsyncClient):
        resp = await client.get("/docs")
        assert "content-security-policy" not in resp.headers


class TestMetricsGuard:
    async def test_metrics_token_enforced(self, client: AsyncClient):
        from app.core.config import get_settings

        settings = get_settings()
        settings.metrics_token = "mtok"
        try:
            assert (await client.get("/metrics/")).status_code == 401
            resp = await client.get("/metrics/", headers={"Authorization": "Bearer mtok"})
            assert resp.status_code == 200
        finally:
            settings.metrics_token = None

    async def test_metrics_open_when_unconfigured(self, client: AsyncClient):
        assert (await client.get("/metrics/")).status_code == 200


class TestAuditRoleAccess:
    async def test_ops_and_finance_can_read_audit(self, client: AsyncClient, sm):
        """审计只读对全部管理角色开放(含 ops/finance)。"""
        from tests.test_catalog import admin_headers

        for role in ("ops", "finance", "readonly"):
            headers = await admin_headers(sm, client, role=role)
            resp = await client.get("/api/admin/v1/audit", headers=headers)
            assert resp.status_code == 200, (role, resp.text)
