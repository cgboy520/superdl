import base64

from httpx import AsyncClient

from tests.helpers import admin_headers, register


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
        # 封禁期内连正确密码也 429:廉价准入先于 bcrypt,封禁中的请求不付哈希成本
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


class TestAccountLevelLock:
    """账号级锁定:撞库可以换 IP,但换不了目标账号。

    直连对端 IP 不可经 HTTP 头伪造(信任边界为直连),测试层换 IP 只能直接调 service。
    """

    async def test_user_login_account_lock_across_ips(self, client: AsyncClient, sm):
        """10 个不同 IP 各失败 1 次(IP+账号桶每桶仅 1,不触发),第 11 次账号桶锁死。"""
        import pytest

        from app.core.errors import AppError, ErrorCode
        from app.modules.account import service as account_service

        await register(client, "13800000081", password="secret123456")
        for i in range(10):
            async with sm() as session:
                with pytest.raises(AppError) as exc_info:
                    await account_service.login(
                        session, "13800000081", None, "wrong-pass", client_ip=f"10.0.0.{i}"
                    )
                assert exc_info.value.code is ErrorCode.LOGIN_FAILED
        # 第 11 个新 IP:IP 桶全冷,但账号桶已满 → 廉价准入在 bcrypt 之前拦下
        async with sm() as session:
            with pytest.raises(AppError) as exc_info:
                await account_service.login(
                    session, "13800000081", None, "wrong-pass", client_ip="10.0.0.99"
                )
            assert exc_info.value.code is ErrorCode.RATE_LIMITED
            assert exc_info.value.http_status == 429

    async def test_admin_login_account_lock_across_ips(self, client: AsyncClient, sm):
        """管理端同款:换 IP 逃不掉目标账号的阶梯锁定。"""
        import pytest

        from app.core.errors import AppError, ErrorCode
        from app.modules.adminapi import service as admin_service

        await admin_headers(sm, client)  # 创建 admin-user(成功登录不计数)
        for i in range(10):
            async with sm() as session:
                with pytest.raises(AppError) as exc_info:
                    await admin_service.login(
                        session, "admin-user", "wrong", client_ip=f"10.1.0.{i}"
                    )
                assert exc_info.value.code is ErrorCode.LOGIN_FAILED
        async with sm() as session:
            with pytest.raises(AppError) as exc_info:
                await admin_service.login(session, "admin-user", "wrong", client_ip="10.1.0.99")
            assert exc_info.value.code is ErrorCode.RATE_LIMITED

    async def test_success_after_foreign_failures_notifies_and_clears(
        self, client: AsyncClient, sm
    ):
        """账号桶有失败记录而本人成功登录:发异常登录通知并清零账号桶。

        正常用户的预算不被攻击者的失败计数拖垮;通知让本人感知撞库。
        """
        import pytest
        from sqlalchemy import select

        from app.core.errors import AppError
        from app.core.ratelimit import read_hits
        from app.modules.account import service as account_service
        from app.modules.notify.models import Notification

        data = await register(client, "13800000082", password="secret123456")
        # 攻击者从另外两个 IP 撞库失败 2 次
        for ip in ("10.2.0.1", "10.2.0.2"):
            async with sm() as session:
                with pytest.raises(AppError):
                    await account_service.login(
                        session, "13800000082", None, "wrong-pass", client_ip=ip
                    )
        assert await read_hits("user-login-acct:13800000082", window_seconds=900.0) == 2
        # 本人成功登录
        async with sm() as session:
            pair = await account_service.login(
                session, "13800000082", None, "secret123456", client_ip="10.2.0.3"
            )
        assert pair.access_token
        async with sm() as session:
            row = (
                await session.execute(
                    select(Notification).where(Notification.user_id == data["user"]["id"])
                )
            ).scalar_one()
            assert "异常登录" in row.title
            assert row.severity == "warning"
        # 账号桶已清零:后续本人登录不再重复通知,也不被历史失败拖垮
        assert await read_hits("user-login-acct:13800000082", window_seconds=900.0) == 0


class TestEnvironmentFailClosed:
    def test_environment_is_required(self, monkeypatch):
        """SUPERDL_ENVIRONMENT 无默认:缺失即拒绝启动(fail-closed)。"""
        import pytest
        from pydantic import ValidationError

        from app.core.config import Settings

        monkeypatch.delenv("SUPERDL_ENVIRONMENT", raising=False)
        with pytest.raises(ValidationError, match="environment"):
            Settings(_env_file=None)  # type: ignore[call-arg]


class TestProdConfigValidation:
    @staticmethod
    def _complete_prod_kwargs() -> dict:
        """能通过 prod 校验的最小配置;各用例在此基础上注入一个坏值。

        只含 provider 选择与基础设施项:短信/验证码凭据不在启动期校验,
        alertmanager_token 与 prometheus_url 只在 lifespan 打 WARNING。
        """
        return {
            "_env_file": None,  # 运行时参数,stub 未暴露
            "environment": "prod",
            # 合法形态:64 字符十六进制(openssl rand -hex 32);prod 校验拒绝占位符与低熵串
            "jwt_secret": "9f4a1c7e2b8d0f63a5e9c417b3d68f02a1c4e7958b0d326f7a9c1e4b58d2f603",
            "sms_provider": "aliyun",
            "k8s_backend": "real",
            "payment_mock": False,
            "database_url": "postgresql+asyncpg://svc:strongpass@pg.internal:5432/superdl?sslmode=require",
            "cors_origins": ["https://console.superdl.cn"],
            "admin_host": "admin.superdl.cn",
            "admin_edge_token": "edge-token-for-tests",
            "jupyter_domain_suffix": "app.superdl.cn",
            "service_domain_suffix": "svc.superdl.cn",
            "public_base_url": "https://api.superdl.cn",
            "metrics_token": "mtoken",
            "config_encryption_key": base64.urlsafe_b64encode(b"k" * 32).decode(),
            "image_allowed_registries": "registry.superdl.internal/",
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

    def test_prod_rejects_placeholder_and_low_entropy_jwt_secret(self):
        """占位符/低熵 JWT 密钥在 prod 拒启:长度够不代表熵够(33 字符的模板占位也只有
        15 个唯一字符);占位密钥 = 任何人按公开模板伪造平台令牌(含 admin audience)。"""
        import pytest
        from pydantic import ValidationError

        from app.core.config import Settings

        for bad in (
            "CHANGE_ME_32_CHARS_MINIMUM_______",  # 仓库模板的历史占位
            "CHANGE_ME",
            "x" * 40,  # 长度足够但唯一字符 1 个
        ):
            kwargs = {**self._complete_prod_kwargs(), "jwt_secret": bad}
            with pytest.raises(ValidationError, match="jwt_secret"):
                Settings(**kwargs)

    def test_prod_accepts_complete_config(self):
        """最小配置过 Settings 校验(本层只管 env;人机验证/实名的 prod 开启是
        lifespan 合规闸门的职责,见 platform_config.assert_prod_compliance_gates)。"""
        from app.core.config import Settings

        s = Settings(**self._complete_prod_kwargs())
        assert s.environment == "prod"
        assert s.real_name_enabled is False
        assert s.sms_access_key_id is None

    def test_prod_worker_role_skips_api_only_secrets(self):
        """worker 不挂 superdl-auth / superdl-edge(secrets 分域):prod 下按角色跳过 jwt_secret 与
        admin_edge_token 两项,其余项照拒;api 角色缺 admin_edge_token 仍拒启。"""
        import pytest
        from pydantic import ValidationError

        from app.core.config import Settings

        worker = {**self._complete_prod_kwargs(), "process_role": "worker"}
        worker.pop("jwt_secret", None)
        worker.pop("admin_edge_token", None)
        assert Settings(**worker).process_role == "worker"
        with pytest.raises(ValidationError, match="sms_provider"):
            Settings(**{**worker, "sms_provider": "mock"})
        # 非 core 组件不挂 superdl-cloud / superdl-payment:渠道 provider 不校验,其余照拒
        tenant_mgr = {
            **worker,
            "worker_component": "tenant-mgr",
            "sms_provider": "mock",
            "payment_mock": True,
        }
        assert Settings(**tenant_mgr).worker_component == "tenant-mgr"
        with pytest.raises(ValidationError, match="k8s_backend"):
            Settings(**{**tenant_mgr, "k8s_backend": "fake"})
        # disk-ops 只挂 db/metrics:连配置主密钥也不校验;tenant-mgr 挂 crypto 则照拒
        assert Settings(
            **{**tenant_mgr, "worker_component": "disk-ops", "config_encryption_key": None}
        )
        with pytest.raises(ValidationError, match="config_encryption_key"):
            Settings(**{**tenant_mgr, "config_encryption_key": None})
        with pytest.raises(ValidationError, match="admin_edge_token"):
            Settings(**{**self._complete_prod_kwargs(), "admin_edge_token": ""})

    def test_required_real_name_without_enabled_rejected_in_any_env(self):
        """充值强制实名而实名认证未开启 = 用户永远完不成实名,任何环境都拒
        (经平台配置在线打开的同一组合由 platform_config 写入侧拦,见 test_platform_config)。"""
        import pytest
        from pydantic import ValidationError

        from app.core.config import Settings

        kwargs = self._complete_prod_kwargs()
        kwargs["real_name_required_for_recharge"] = True
        with pytest.raises(ValidationError, match="real_name_enabled"):
            Settings(**kwargs)
        with pytest.raises(ValidationError, match="real_name_enabled"):
            Settings(
                _env_file=None,  # pyright: ignore[reportCallIssue] - 运行时参数,stub 未暴露
                environment="dev",
                real_name_required_for_recharge=True,
            )
        kwargs["real_name_enabled"] = True
        assert Settings(**kwargs).real_name_required_for_recharge is True

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

    def test_cors_wildcard_rejected_in_any_env(self):
        """CORS 通配 + allow_credentials=true = 带凭证全网放行;任何环境都拒。"""
        import pytest
        from pydantic import ValidationError

        from app.core.config import Settings

        with pytest.raises(ValidationError, match="cors_origins"):
            Settings(
                _env_file=None,  # pyright: ignore[reportCallIssue] - 运行时参数,stub 未暴露
                environment="dev",
                cors_origins=["*"],
            )

    def test_prod_nonlocal_db_requires_tls(self):
        """prod 非本机 PG 无 sslmode=require 即拒启(零信任网络;JuiceFS 早已同口径)。"""
        import pytest
        from pydantic import ValidationError

        from app.core.config import Settings

        kwargs = self._complete_prod_kwargs()
        kwargs["database_url"] = "postgresql+asyncpg://svc:strongpass@pg.internal:5432/superdl"
        with pytest.raises(ValidationError, match="sslmode"):
            Settings(**kwargs)
        kwargs["database_url"] = (
            "postgresql+asyncpg://svc:strongpass@pg.internal:5432/superdl?sslmode=verify-full"
        )
        assert Settings(**kwargs).environment == "prod"
        # 本机回环豁免(单节点/开发机房形态)
        kwargs["database_url"] = "postgresql+asyncpg://svc:strongpass@127.0.0.1:5432/superdl"
        assert Settings(**kwargs).environment == "prod"


class TestDbTlsTranslate:
    """db._split_db_tls:asyncpg 不认 libpq 的 sslmode 参数名,URL 查询串必须翻译。"""

    def test_sslmode_translated_to_ssl_connect_arg(self):
        from app.core.db import _split_db_tls

        url, args = _split_db_tls("postgresql+asyncpg://u:p@h:5432/d?sslmode=require")
        assert args == {"ssl": "require"}
        assert "sslmode" not in url

    def test_no_sslmode_passthrough(self):
        from app.core.db import _split_db_tls

        url = "postgresql+asyncpg://u:p@localhost:5432/d"
        assert _split_db_tls(url) == (url, {})


class TestSmsCodeBruteForce:
    async def test_code_burned_after_max_attempts(self, client: AsyncClient, sm):
        """同一条验证码失败 5 次后作废:used_at 落库,正确码也不再放行(计次持久化于 DB)。"""
        import pytest

        from app.core.errors import AppError
        from app.modules.account import service as account_service
        from app.modules.account.models import SmsCode

        phone = "13800000088"
        resp = await client.post(
            "/api/v1/auth/sms-code",
            json={"phone": phone, "purpose": "register"},
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
        await client.post(
            "/api/v1/auth/sms-code",
            json={"phone": phone, "purpose": "register"},
        )
        async with sm() as session:
            await account_service._consume_sms_code(session, phone, "123456", "register")
            await session.commit()
        async with sm() as session:
            with pytest.raises(AppError):
                await account_service._consume_sms_code(session, phone, "123456", "register")

    async def test_sms_login_rate_limited(self, client: AsyncClient):
        """验证码登录路径与密码路径同限流(否则可穷举 6 位码)。"""
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
                json={
                    "phone": f"138000001{i:02d}",
                    "purpose": "register",
                },
            )
            assert resp.status_code == 204
        resp = await client.post(
            "/api/v1/auth/sms-code",
            json={"phone": "13800000199", "purpose": "register"},
        )
        assert resp.status_code == 429

    async def test_concurrent_same_phone_send_serialized(self, client: AsyncClient, sm):
        """同号退避的 TOCTOU 回归:两请求并发,一个放行,另一个必撞递增退避 429。

        无 pg_advisory_xact_lock 时,两请求可双双通过「先查」各发一条(轰炸/成本)。
        """
        import asyncio

        from app.core.errors import AppError, ErrorCode
        from app.modules.account import service as account_service

        async def send() -> None:
            async with sm() as session:
                await account_service.send_sms_code(
                    session, "13800000093", "register", client_ip="10.9.0.1"
                )

        results = await asyncio.gather(send(), send(), return_exceptions=True)
        oks = [r for r in results if r is None]
        limited = [
            r for r in results if isinstance(r, AppError) and r.code is ErrorCode.SMS_TOO_FREQUENT
        ]
        assert len(oks) == 1
        assert len(limited) == 1


class TestRequestBodyLimit:
    """请求体硬上限(1MiB):双层防御的内层(外层 Envoy requestBuffer,测试只守内层)。"""

    async def test_over_limit_declared_content_length_fast_413(self, client: AsyncClient):
        """Content-Length 已超限:不读 body 直接 413,统一错误体 + 安全头仍在
        (证明中间件位置在 SecurityHeaders/Observability 之内)。"""
        resp = await client.post(
            "/api/v1/auth/login",
            content=b"x" * (1024 * 1024 + 1),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 413
        body = resp.json()
        assert body["code"] == "PAYLOAD_TOO_LARGE"
        assert body["message_key"] == "common.payloadTooLarge"
        assert resp.headers["x-content-type-options"] == "nosniff"

    async def test_over_limit_chunked_stream_413(self, client: AsyncClient):
        """无 Content-Length(chunked)的攻击面:流式计数超限同样 413 短路。"""

        async def stream():
            chunk = b"y" * (256 * 1024)
            for _ in range(5):  # 1.25Mi,无 Content-Length
                yield chunk

        resp = await client.post(
            "/api/v1/auth/login",
            content=stream(),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 413
        assert resp.json()["code"] == "PAYLOAD_TOO_LARGE"

    async def test_exact_limit_passes_to_router(self, client: AsyncClient):
        """边界:恰好 1MiB 放行进路由(JSON 解析失败归 400 系,绝不是 413)。"""
        resp = await client.post(
            "/api/v1/auth/login",
            content=b"z" * (1024 * 1024),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code != 413
        assert resp.status_code in (400, 422)

    async def test_normal_request_unaffected(self, client: AsyncClient):
        resp = await client.get("/healthz")
        assert resp.status_code == 200


class TestSecurityHeaders:
    async def test_headers_on_api_responses(self, client: AsyncClient):
        resp = await client.get("/healthz")
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["x-frame-options"] == "DENY"
        assert "default-src 'none'" in resp.headers["content-security-policy"]
        assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert "camera=()" in resp.headers["permissions-policy"]
        assert resp.headers["cross-origin-opener-policy"] == "same-origin"
        # same-site(非同源):console/admin 与 api 是同站兄弟子域
        assert resp.headers["cross-origin-resource-policy"] == "same-site"

    async def test_docs_exempt_from_csp(self, client: AsyncClient):
        resp = await client.get("/docs")
        assert "content-security-policy" not in resp.headers


class TestEdgeGuard:
    """prod 边缘收口(恒开,无开关):/api/admin 与 /metrics 不从公网 api 域暴露。"""

    async def test_admin_api_hidden_from_public_host(self, client: AsyncClient, monkeypatch):
        from app.core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "environment", "prod", raising=False)
        monkeypatch.setattr(settings, "admin_host", "admin.superdl.cn", raising=False)
        monkeypatch.setattr(settings, "admin_edge_token", "edge-secret-1", raising=False)
        # 公网 api 域:管理端登录面 404(不暴露)
        resp = await client.post(
            "/api/admin/v1/auth/login",
            json={"username": "x", "password": "y"},
            headers={"Host": "api.superdl.cn"},
        )
        assert resp.status_code == 404
        # admin 域 + 边缘密钥头(admin SPA 同源反代注入):穿过收口,到达路由(凭据错 400)
        resp = await client.post(
            "/api/admin/v1/auth/login",
            json={"username": "x", "password": "y"},
            headers={"Host": "admin.superdl.cn", "X-Admin-Edge-Token": "edge-secret-1"},
        )
        assert resp.status_code == 400
        # 用户端 API 不受影响
        assert (await client.get("/healthz", headers={"Host": "api.superdl.cn"})).status_code == 200

    async def test_admin_api_rejects_host_match_without_edge_token(
        self, client: AsyncClient, monkeypatch
    ):
        """双闸:Host 伪造正确但缺/错共享密钥头(集群内直连)→ 404,不穿闸。"""
        from app.core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "environment", "prod", raising=False)
        monkeypatch.setattr(settings, "admin_host", "admin.superdl.cn", raising=False)
        monkeypatch.setattr(settings, "admin_edge_token", "edge-secret-2", raising=False)
        # 缺头
        resp = await client.post(
            "/api/admin/v1/auth/login",
            json={"username": "x", "password": "y"},
            headers={"Host": "admin.superdl.cn"},
        )
        assert resp.status_code == 404
        # 错头
        resp = await client.post(
            "/api/admin/v1/auth/login",
            json={"username": "x", "password": "y"},
            headers={"Host": "admin.superdl.cn", "X-Admin-Edge-Token": "wrong"},
        )
        assert resp.status_code == 404

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


class TestMetricsGuard:
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


class TestSmsCodeAtRest:
    async def test_code_is_not_stored_in_clear(self, client, sm):
        """库里不能有验证码明文:一次只读 DB 访问就能拿到全部活跃验证码。"""
        from sqlalchemy import select

        from app.modules.account.models import SmsCode

        resp = await client.post(
            "/api/v1/auth/sms-code",
            json={"phone": "13800000777", "purpose": "register"},
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
    def test_unknown_superdl_vars_reported(self, monkeypatch):
        """拼错/残留的 SUPERDL_* 变量会被 pydantic 静默忽略:启动扫描负责把它们揪出来。"""
        from app.core.config import unknown_superdl_env_keys

        monkeypatch.setenv("SUPERDL_JWT_SECRET", "x")  # 合法键
        monkeypatch.setenv("SUPERDL_JWT_SECERT", "typo")  # 拼写错误
        monkeypatch.setenv("SUPERDL_OLD_REMOVED_KEY", "y")  # 改名残留
        unknown = unknown_superdl_env_keys()
        assert "SUPERDL_JWT_SECERT" in unknown
        assert "SUPERDL_OLD_REMOVED_KEY" in unknown
        assert "SUPERDL_JWT_SECRET" not in unknown


class TestBootstrapAdminGate:
    async def test_bootstrap_password_policy_at_service_layer(self, sm):
        """seed_dev 走的引导入口自查口令:过短/超 72 字节都拒,合规才建号。"""
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


class TestAuditGate:
    async def test_consecutive_audit_failures_fail_closed(self, client: AsyncClient, monkeypatch):
        """审计写持续失败超阈值 → 写操作 fail-closed 503;读不受影响;探活恢复即复位。"""
        from app.core import audit as audit_mod

        class _Boom:
            def __call__(self) -> object:
                raise RuntimeError("audit db down (injected)")

        monkeypatch.setattr(audit_mod, "get_sessionmaker", _Boom())
        # 每试一号:登录失败计数按 IP+手机号分桶,同号连试会被登录限流(429)抢先
        for i in range(audit_mod.AUDIT_FAIL_CLOSED_THRESHOLD):
            resp = await client.post(
                "/api/v1/auth/login", json={"phone": f"138{i:08d}", "password": "x"}
            )
            assert resp.status_code == 400  # 抖动期 fail-open:业务照常,失败只计数
        # 超阈值:写 fail-closed(统一错误体)
        resp = await client.post("/api/v1/auth/login", json={"phone": "13800000000"})
        assert resp.status_code == 503
        assert resp.json()["code"] == "AUDIT_UNAVAILABLE"
        # 读与基础设施路径不受影响
        assert (await client.get("/api/v1/auth/captcha-config")).status_code == 200
        assert (await client.get("/healthz")).status_code == 200
        monkeypatch.undo()
        # 探活自愈:恢复后第一个写请求探活成功 → 复位放行(回到业务错误而非 503)
        resp = await client.post("/api/v1/auth/login", json={"phone": "13800000000"})
        assert resp.status_code == 400


class TestAdminLogout:
    async def test_logout_revokes_all_sessions(self, client: AsyncClient, sm):
        """服务端登出:token_version+1,已签发 token 即刻失效(含本会话)。"""
        ah = await admin_headers(sm, client, role="ops")
        assert (await client.get("/api/admin/v1/me", headers=ah)).status_code == 200
        resp = await client.post("/api/admin/v1/auth/logout", headers=ah)
        assert resp.status_code == 204
        assert (await client.get("/api/admin/v1/me", headers=ah)).status_code == 401


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
        Uniform500Middleware 在链内层渲染 500(异常不穿透审计中间件),
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
