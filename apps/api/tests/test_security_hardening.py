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


class TestProdDocsClosed:
    def test_prod_disables_docs_and_openapi_routes(self, monkeypatch):
        """生产不对公网暴露 /docs 与 /openapi.json。

        管理端 51 条路径、每个字段名与取值范围(调账、补单、平台配置)都在 schema 里,
        免登录可读等于把侦察成本降到零。只关 docs_url 不够 —— openapi_url 仍会吐出整份
        schema。export_openapi 走 app.openapi() 直取,不经这些路由,契约闸门不受影响。
        """
        from app.core.config import get_settings
        from app.main import create_app

        settings = get_settings()
        monkeypatch.setattr(settings, "environment", "prod", raising=False)
        app = create_app()
        assert app.docs_url is None
        assert app.redoc_url is None
        assert app.openapi_url is None
        assert app.openapi()["paths"]  # schema 本身照常可导出


class TestTenantContainerHardening:
    def test_security_context_is_unconditional(self):
        """租户容器加固不看 runtimeClass、不看发行版、不看档位。

        此前是 `V1SecurityContext(...) if spec.runtime_class is None else None`,
        把 runtime_class 当成「是不是 Kata」的代理判据;k3s 共享档为了拿 nvidia 运行时
        把它设成了 "nvidia",于是全站最不可信的负载(runc + HAMi 软切分,与其他租户共享
        同一内核和同一张物理 GPU)整段 securityContext 被跳过,只剩 userns 一层。
        现有 test_cluster_status 只断言了 runtime_class,正是漏网处。
        """
        from app.core.k8s.real import tenant_security_context

        ctx = tenant_security_context()
        assert ctx.allow_privilege_escalation is False
        assert ctx.capabilities is not None and ctx.capabilities.drop == ["ALL"]
        assert ctx.seccomp_profile is not None and ctx.seccomp_profile.type == "RuntimeDefault"

    def test_k3s_shared_still_gets_userns_and_hardening(self):
        """k3s 共享档:runtimeClassName=nvidia,但 userns 与容器加固都不能因此消失。"""
        from app.core.gpu_adapter import build_gpu_request

        req = build_gpu_request(
            tier="shared_std",
            gpu_count=1,
            gpu_cores_pct=50,
            vram_gb=8,
            mig_profile=None,
            pool_label="hami",
            distro="k3s",
        )
        assert req.runtime_class == "nvidia"
        assert req.host_users is False


class TestSmsCodeAtRest:
    async def test_code_is_not_stored_in_clear(self, client, sm):
        """库里不能有验证码明文。

        密码走了 bcrypt,验证码此前是明文:一次只读数据库访问(备份 dump、只读副本、
        DBA 账号、一个注入落点)就能 SELECT phone, code 拿到全部活跃验证码,直接登入任意
        账号或走改密路径把本人踢下线 —— 从「读到库」一步升级成「成为任何人」。
        """
        from sqlalchemy import select

        from app.modules.account.models import SmsCode

        resp = await client.post(
            "/api/v1/auth/sms-code", json={"phone": "13800000777", "purpose": "register"}
        )
        assert resp.status_code == 204, resp.text
        async with sm() as session:
            row = (
                await session.execute(select(SmsCode).where(SmsCode.phone == "13800000777"))
            ).scalar_one()
        # mock 渠道固定发 123456
        assert "123456" not in row.code_hash
        assert len(row.code_hash) == 64
        # 域分离:同一个码换个手机号/用途摘要必须不同,否则可以跨账号搬运
        from app.core.crypto import hash_sms_code

        assert row.code_hash == hash_sms_code("13800000777", "register", "123456")
        assert hash_sms_code("13800000778", "register", "123456") != row.code_hash
        assert hash_sms_code("13800000777", "login", "123456") != row.code_hash

    async def test_hashed_code_still_verifies(self, client, sm):
        """改成摘要之后,注册这条正常路径必须照常走通。"""
        resp = await client.post(
            "/api/v1/auth/sms-code", json={"phone": "13800000778", "purpose": "register"}
        )
        assert resp.status_code == 204
        ok = await client.post(
            "/api/v1/auth/register",
            json={
                "phone": "13800000778",
                "sms_code": "123456",
                "password": "secret123",
                "accept_terms": True,
            },
        )
        assert ok.status_code == 201, ok.text
