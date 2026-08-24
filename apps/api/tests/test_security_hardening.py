import base64

from httpx import AsyncClient

from tests.test_catalog import admin_headers


class TestLoginRateLimit:
    async def test_admin_login_locked_after_5_attempts(self, client: AsyncClient, sm):
        await admin_headers(sm, client)  # 创建 admin-user(成功登录不计数)
        for _ in range(5):
            resp = await client.post(
                "/api/admin/v1/auth/login", json={"username": "admin-user", "password": "wrong"}
            )
            assert resp.json()["code"] == "LOGIN_FAILED"
        resp = await client.post(
            "/api/admin/v1/auth/login", json={"username": "admin-user", "password": "wrong"}
        )
        assert resp.status_code == 429
        assert resp.json()["code"] == "RATE_LIMITED"
        # 限流响应必须告诉客户端窗口剩余秒数(Retry-After)
        assert resp.headers["retry-after"].isdigit()
        # 封禁期内连正确密码也 429:廉价准入先于 bcrypt,封禁中的请求不再付哈希成本
        resp = await client.post(
            "/api/admin/v1/auth/login", json={"username": "admin-user", "password": "pass1234"}
        )
        assert resp.status_code == 429
        assert resp.json()["code"] == "RATE_LIMITED"

    async def test_admin_login_pure_ip_bucket(self, client: AsyncClient, sm, monkeypatch):
        """纯 IP 桶:遍历用户名换账号桶也躲不开;只计失败,阈值放宽防误伤 NAT 出口。"""
        from app.modules.adminapi import service as admin_service

        monkeypatch.setattr(admin_service, "LOGIN_IP_MAX_ATTEMPTS", 3)
        await admin_headers(sm, client)
        # 每次换用户名:账号桶每桶仅 1 次,远不到 5;压力全落在纯 IP 桶上
        for i in range(3):
            resp = await client.post(
                "/api/admin/v1/auth/login", json={"username": f"spray{i}", "password": "wrong"}
            )
            assert resp.json()["code"] == "LOGIN_FAILED"
        resp = await client.post(
            "/api/admin/v1/auth/login", json={"username": "spray3", "password": "wrong"}
        )
        assert resp.status_code == 429
        assert resp.json()["code"] == "RATE_LIMITED"

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
        s = Settings(_env_file=None, environment="dev")  # type: ignore[call-arg]
        assert s.bootstrap_admin_password is None


class TestEnvironmentFailClosed:
    def test_environment_is_required(self, monkeypatch):
        """SUPERDL_ENVIRONMENT 无默认:缺失即拒绝启动(fail-closed)。"""
        import pytest
        from pydantic import ValidationError

        from app.core.config import Settings

        monkeypatch.delenv("SUPERDL_ENVIRONMENT", raising=False)
        with pytest.raises(ValidationError, match="environment"):
            Settings(_env_file=None)  # type: ignore[call-arg]

    def test_real_backend_requires_prod(self):
        """k8s_backend=real + 非 prod 环境 = 宽松默认(mock 支付/固定短信码)暴露在真实集群。"""
        import pytest
        from pydantic import ValidationError

        from app.core.config import Settings

        for env in ("dev", "test"):
            with pytest.raises(ValidationError, match="SUPERDL_ENVIRONMENT=prod"):
                Settings(_env_file=None, environment=env, k8s_backend="real")  # type: ignore[call-arg]

    def test_fake_backend_allows_dev(self):
        from app.core.config import Settings

        s = Settings(_env_file=None, environment="dev", k8s_backend="fake")  # type: ignore[call-arg]
        assert s.environment == "dev"


class TestProdConfigValidation:
    @staticmethod
    def _complete_prod_kwargs() -> dict:
        """一套能通过 prod 校验的完整配置;各用例在此基础上注入一个坏值。"""
        return {
            "_env_file": None,  # 运行时参数,stub 未暴露
            "environment": "prod",
            "jwt_secret": "x" * 40,
            "sms_provider": "aliyun",
            "sms_access_key_id": "ak",
            "sms_access_key_secret": "sk",
            "sms_sign_name": "SuperDL",
            "sms_template_verify": "SMS_1",
            "sms_template_notice": "SMS_2",
            "k8s_backend": "real",
            "payment_mock": False,
            "database_url": "postgresql+asyncpg://svc:strongpass@pg.internal:5432/superdl",
            "cors_origins": ["https://console.superdl.cn"],
            "ssh_host": "ssh1.superdl.cn",
            "admin_host": "admin.superdl.cn",
            "jupyter_domain_suffix": "app.superdl.cn",
            "public_base_url": "https://api.superdl.cn",
            "prometheus_url": "http://kube-prometheus-stack-prometheus.monitoring.svc:9090",
            "alertmanager_token": "token",
            "metrics_token": "mtoken",
            "config_encryption_key": base64.urlsafe_b64encode(b"k" * 32).decode(),
            "image_allowed_registries": ["registry.superdl.internal/"],
        }

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

        s = Settings(**self._complete_prod_kwargs())
        assert s.environment == "prod"

    def test_prod_rejects_localhost_prometheus(self):
        """prometheus_url 保持本地默认会静默失效(计费无恙但面板/对账全空),prod 必拒。"""
        import pytest as _pytest

        from app.core.config import Settings

        kwargs = self._complete_prod_kwargs()
        del kwargs["prometheus_url"]  # 回落默认值 http://localhost:9090
        with _pytest.raises(ValueError, match="prometheus_url"):
            Settings(**kwargs)

    def test_prod_rejects_bootstrap_admin_password(self):
        """引导口令是一次性 dev 工具:带进 prod 说明运维忘了删,启动即拒并给出正确做法。"""
        import pytest
        from pydantic import ValidationError

        from app.core.config import Settings

        with pytest.raises(ValidationError, match="bootstrap_admin_password"):
            Settings(**self._complete_prod_kwargs(), bootstrap_admin_password="bootstrap-123")

    def test_prod_rejects_mock_realname_when_required(self):
        """充值强制实名 + mock 渠道 = 实名形同虚设(mock 核验恒过),prod 必拒。"""
        import pytest
        from pydantic import ValidationError

        from app.core.config import Settings

        with pytest.raises(ValidationError, match="real_name_provider"):
            Settings(**self._complete_prod_kwargs(), real_name_required_for_recharge=True)

    def test_prod_allows_mock_realname_when_not_required(self):
        """实名开关未启用时 mock 无害,不过度收紧。"""
        from app.core.config import Settings

        s = Settings(**self._complete_prod_kwargs())
        assert s.real_name_provider == "mock"

    def test_prod_rejects_empty_image_allowed_registries(self):
        """空白名单 = 租户可拉任意仓库镜像(把任意镜像引进集群),prod 必须显式配置。"""
        import pytest
        from pydantic import ValidationError

        from app.core.config import Settings

        kwargs = self._complete_prod_kwargs()
        del kwargs["image_allowed_registries"]
        with pytest.raises(ValidationError, match="image_allowed_registries"):
            Settings(**kwargs)

    def test_prod_alipay_enabled_requires_seller_id(self):
        """prod 启用支付宝但缺收款方 PID:回调无法核对收款账号,启动即拒。"""
        import pytest
        from pydantic import ValidationError

        from app.core.config import Settings

        with pytest.raises(ValidationError, match="alipay_seller_id"):
            Settings(**self._complete_prod_kwargs(), payment_alipay_enabled=True)
        s = Settings(
            **self._complete_prod_kwargs(),
            payment_alipay_enabled=True,
            alipay_seller_id="2088123456789012",
        )
        assert s.payment_alipay_enabled is True


class TestSmsCodeBruteForce:
    async def test_code_burned_after_max_attempts(self, client: AsyncClient, sm):
        """同一条验证码失败 5 次后作废:used_at 落库,正确码也不再放行(计次持久化于 DB)。"""
        import pytest

        from app.core.errors import AppError
        from app.modules.account import service as account_service
        from app.modules.account.models import SmsCode

        phone = "13800000088"
        resp = await client.post(
            "/api/v1/auth/sms-code", json={"phone": phone, "purpose": "register"}
        )
        assert resp.status_code == 204
        for _ in range(5):
            async with sm() as session:
                with pytest.raises(AppError):
                    await account_service._consume_sms_code(session, phone, "000000", "register")
        # 达上限即置 used_at:烧毁的行不再满足 used_at IS NULL,不会被反复选中
        from sqlalchemy import select

        async with sm() as session:
            row = (
                await session.execute(
                    select(SmsCode).where(SmsCode.phone == phone).order_by(SmsCode.id.desc())
                )
            ).scalar_one()
        assert row.used_at is not None
        async with sm() as session:
            with pytest.raises(AppError):  # mock 固定码 123456 本是正确码
                await account_service._consume_sms_code(session, phone, "123456", "register")

    async def test_code_is_single_use(self, client: AsyncClient, sm):
        """消费成功的验证码必须立刻作废:同一条码不能注册出第二个账号。"""
        import pytest

        from app.core.errors import AppError
        from app.modules.account import service as account_service

        phone = "13800000090"
        await client.post("/api/v1/auth/sms-code", json={"phone": phone, "purpose": "register"})
        async with sm() as session:
            await account_service._consume_sms_code(session, phone, "123456", "register")
            await session.commit()
        async with sm() as session:
            with pytest.raises(AppError):
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


class TestEdgeGuard:
    """prod 边缘收口:/api/admin 与 /metrics 不从公网 api 域暴露。"""

    async def test_admin_api_hidden_from_public_host(self, client: AsyncClient, monkeypatch):
        from app.core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "environment", "prod", raising=False)
        monkeypatch.setattr(settings, "admin_host", "admin.superdl.cn", raising=False)
        # 公网 api 域:管理端登录面 404(不暴露)
        resp = await client.post(
            "/api/admin/v1/auth/login",
            json={"username": "x", "password": "y"},
            headers={"Host": "api.superdl.cn"},
        )
        assert resp.status_code == 404
        # admin 域(admin SPA 同源反代):穿过收口,到达路由(凭据错 400,不是 404)
        resp = await client.post(
            "/api/admin/v1/auth/login",
            json={"username": "x", "password": "y"},
            headers={"Host": "admin.superdl.cn"},
        )
        assert resp.status_code == 400
        # 用户端 API 不受影响
        assert (await client.get("/healthz", headers={"Host": "api.superdl.cn"})).status_code == 200

    async def test_metrics_rejects_ingress_traffic(self, client: AsyncClient, monkeypatch):
        from app.core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "environment", "prod", raising=False)
        monkeypatch.setattr(settings, "metrics_token", "mtok", raising=False)
        # 经 ingress(带 XFF)→ 404;集群内直刮 → 到达 Bearer 校验(无 token 401)
        assert (
            await client.get("/metrics/", headers={"X-Forwarded-For": "1.2.3.4"})
        ).status_code == 404
        assert (await client.get("/metrics/")).status_code == 401
        assert (
            await client.get("/metrics/", headers={"Authorization": "Bearer mtok"})
        ).status_code == 200

    async def test_non_prod_not_guarded(self, client: AsyncClient):
        # test 环境无 ingress(Host 是 testserver),收口不启用:admin 路由照常到达
        resp = await client.post(
            "/api/admin/v1/auth/login", json={"username": "x", "password": "y"}
        )
        assert resp.status_code == 400


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

    async def test_non_ascii_authorization_rejected_not_500(self, client: AsyncClient):
        """非 ASCII 的 Authorization 头:compare_digest 收 str 会抛 TypeError,必须先 encode。"""
        from app.core.config import get_settings

        settings = get_settings()
        settings.metrics_token = "mtok"
        try:
            resp = await client.get(
                "/metrics/",
                headers={b"authorization": "Bearer caf\u00e9".encode("latin-1")},
            )
            assert resp.status_code == 401
        finally:
            settings.metrics_token = None


class TestProdDocsClosed:
    def test_prod_disables_docs_and_openapi_routes(self, monkeypatch):
        """生产不对公网暴露 /docs 与 /openapi.json。

        只关 docs_url 不够:openapi_url 仍会吐出整份管理端 schema。
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

        禁止拿 runtime_class 当「是不是 Kata」的代理判据:k3s 共享档也带 runtimeClassName。
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
        """库里不能有验证码明文:一次只读 DB 访问就能拿到全部活跃验证码。"""
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


class TestGhostEnvKeys:
    def test_unknown_superdl_vars_reported(self):
        """拼错/残留的 SUPERDL_* 变量会被 pydantic 静默忽略:启动扫描负责把它们揪出来。"""
        from app.core.config import unknown_superdl_env_keys

        env = {
            "SUPERDL_JWT_SECRET": "x",  # 合法键
            "SUPERDL_RKE2_VERSION": "v1",  # AliasChoices 别名键
            "SUPERDL_JWT_SECERT": "typo",  # 拼写错误
            "SUPERDL_OLD_REMOVED_KEY": "y",  # 改名残留
            "DATABASE_URL": "z",  # 非本前缀,不管
        }
        assert unknown_superdl_env_keys(env) == [
            "SUPERDL_JWT_SECERT",
            "SUPERDL_OLD_REMOVED_KEY",
        ]


class TestBootstrapAdminGate:
    async def test_bootstrap_password_policy_at_service_layer(self, sm):
        """服务层自查(不经 lifespan):过短/超 72 字节都拒,合规才建号。"""
        import pytest
        from sqlalchemy import select

        from app.modules.adminapi.models import AdminUser
        from app.modules.adminapi.service import ensure_bootstrap_admin

        async with sm() as session:
            with pytest.raises(RuntimeError, match="引导口令"):
                await ensure_bootstrap_admin(session, "short")
        async with sm() as session:
            with pytest.raises(RuntimeError, match="引导口令"):
                await ensure_bootstrap_admin(session, "汉" * 25)  # 75 字节
        async with sm() as session:
            await ensure_bootstrap_admin(session, "l0ng-enough-pass")
            admins = (await session.execute(select(AdminUser))).scalars().all()
        assert [a.username for a in admins] == ["admin"]

    async def test_bootstrap_creates_admin_in_dev(self, client: AsyncClient, sm, monkeypatch):
        """dev + 口令 → 创建首管并可登录;口令是一次性变量,建完即应删除。"""
        from app.core.config import get_settings
        from app.main import create_app, lifespan

        settings = get_settings()
        monkeypatch.setattr(settings, "environment", "dev", raising=False)
        monkeypatch.setattr(
            settings, "bootstrap_admin_password", "bootstrap-pass-123", raising=False
        )
        async with lifespan(create_app()):
            pass
        resp = await client.post(
            "/api/admin/v1/auth/login",
            json={"username": "admin", "password": "bootstrap-pass-123"},
        )
        assert resp.status_code == 200

    async def test_bootstrap_skipped_outside_dev(self, sm, monkeypatch):
        """非 dev 环境带引导口令:跳过创建(prod 在配置校验层已直接拒启动)。"""
        from sqlalchemy import select

        from app.core.config import get_settings
        from app.main import create_app, lifespan
        from app.modules.adminapi.models import AdminUser

        # conftest 已把 environment 钉为 test
        monkeypatch.setattr(
            get_settings(), "bootstrap_admin_password", "bootstrap-pass-123", raising=False
        )
        async with lifespan(create_app()):
            pass
        async with sm() as session:
            rows = (await session.execute(select(AdminUser))).scalars().all()
        assert rows == []

    async def test_bootstrap_short_password_rejected(self, sm, monkeypatch):
        """弱口令引导拒绝启动(与管理端创建管理员的 min_length=12 对齐)。"""
        import pytest

        from app.core.config import get_settings
        from app.main import create_app, lifespan

        settings = get_settings()
        monkeypatch.setattr(settings, "environment", "dev", raising=False)
        monkeypatch.setattr(settings, "bootstrap_admin_password", "short", raising=False)
        with pytest.raises(RuntimeError, match="12"):
            async with lifespan(create_app()):
                pass


class TestUnifiedErrorBodyForHttpException:
    async def test_404_returns_unified_body(self, client: AsyncClient):
        """路由层 404(框架异常)也要是统一错误体:前端只认一种错误形状。"""
        resp = await client.get("/api/v1/no-such-route")
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "NOT_FOUND"
        assert body["message_key"] == "common.notFound"
        assert body["message"] == "资源不存在"

    async def test_405_returns_unified_body_and_allow_header(self, client: AsyncClient):
        resp = await client.post("/healthz")  # 仅注册了 GET
        assert resp.status_code == 405
        body = resp.json()
        assert body["code"] == "METHOD_NOT_ALLOWED"
        assert body["message_key"] == "common.methodNotAllowed"
        assert "GET" in resp.headers["allow"]


class TestAdminTokenRenewal:
    async def test_renew_issues_usable_token(self, client: AsyncClient, sm):
        ah = await admin_headers(sm, client, role="ops")
        old = ah["Authorization"].removeprefix("Bearer ")
        resp = await client.post("/api/admin/v1/auth/refresh", json={"access_token": old})
        assert resp.status_code == 200
        new = resp.json()["access_token"]
        assert new != old
        me = await client.get("/api/admin/v1/me", headers={"Authorization": f"Bearer {new}"})
        assert me.status_code == 200

    async def test_renew_rejects_garbage_and_user_token(self, client: AsyncClient):
        garbage = await client.post("/api/admin/v1/auth/refresh", json={"access_token": "xx"})
        assert garbage.status_code == 401
        # 用户端 token 不可换管理端(audience 物理隔离)
        from tests.test_account_auth import register

        data = await register(client, "13900000071")
        cross = await client.post(
            "/api/admin/v1/auth/refresh", json={"access_token": data["access_token"]}
        )
        assert cross.status_code == 401

    async def test_renew_grace_and_absolute_cap(self, client: AsyncClient, sm):
        """过期 15 分钟宽限内可续;首次登录超 12h(sess_iat 锚定)必须重新登录。"""
        from datetime import timedelta

        from app.core.security import create_token
        from app.core.timeutil import now_utc
        from app.modules.adminapi.service import SESSION_MAX_SECONDS, create_admin

        async with sm() as session:
            admin = await create_admin(session, "grace-admin", "pass1234", "ops")
        ver = admin.token_version
        # 过期 5 分钟(宽限内):可续
        expired = create_token(
            str(admin.id),
            "admin",
            extra={"ver": ver},
            iat=now_utc() - timedelta(seconds=3600 + 300),
        )
        resp = await client.post("/api/admin/v1/auth/refresh", json={"access_token": expired})
        assert resp.status_code == 200
        # sess_iat 超 12h:即使当前 token 未过期也拒(绝对会话上限跨续期链生效)
        ancient = create_token(
            str(admin.id),
            "admin",
            extra={
                "ver": ver,
                "sess_iat": int(
                    (now_utc() - timedelta(seconds=SESSION_MAX_SECONDS + 60)).timestamp()
                ),
            },
        )
        resp2 = await client.post("/api/admin/v1/auth/refresh", json={"access_token": ancient})
        assert resp2.status_code == 401
        # token_version 变(改密/停用)即不可续
        async with sm() as session:
            admin.token_version += 1
            session.add(admin)
            await session.commit()
        resp3 = await client.post("/api/admin/v1/auth/refresh", json={"access_token": expired})
        assert resp3.status_code == 401


class TestAuthenticateHeader:
    async def test_401_carries_www_authenticate(self, client: AsyncClient):
        """RFC 6750:Bearer 鉴权失败必须回 WWW-Authenticate,客户端据此识别挑战。"""
        resp = await client.get("/api/v1/notifications")
        assert resp.status_code == 401
        assert resp.headers["www-authenticate"] == "Bearer"
        assert resp.json()["code"] == "UNAUTHORIZED"


class TestAuditOnUnhandledException:
    async def test_500_is_audited(self, sm):
        """未捕获异常(result=500)也要落审计行:500 恰恰是最需要留痕的结果。
        Uniform500Middleware 在链内层渲染 500(异常不再穿透审计中间件),
        审计走正常响应路径留痕,result 仍为 500。"""
        from httpx import ASGITransport
        from sqlalchemy import select

        from app.core.audit import AuditLog
        from app.main import create_app

        app = create_app()

        @app.post("/api/v1/__boom", include_in_schema=False)
        async def _boom() -> None:  # pyright: ignore[reportUnusedFunction]
            raise RuntimeError("boom")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/v1/__boom")
        assert resp.status_code == 500
        async with sm() as session:
            rows = (
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.action == "POST /api/v1/__boom")
                    )
                )
                .scalars()
                .all()
            )
        assert [r.result for r in rows] == [500]
