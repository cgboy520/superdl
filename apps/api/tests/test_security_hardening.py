# pyright: reportPrivateUsage=false
import base64

from httpx import AsyncClient

from app.core.handles import Handle
from app.modules.account import verification
from tests.helpers import admin_headers, register


class TestLoginRateLimit:
    async def test_admin_login_locked_after_5_attempts(self, client: AsyncClient, sm):
        await admin_headers(sm, client)
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
        assert resp.headers["retry-after"].isdigit()
        resp = await client.post(
            "/api/admin/v1/auth/login", json={"username": "admin-user", "password": "pass1234"}
        )
        assert resp.status_code == 429
        assert resp.json()["code"] == "RATE_LIMITED"

    async def test_admin_login_pure_ip_bucket(self, client: AsyncClient, sm, monkeypatch):
        """Pure IP bucket: rotating usernames does not escape it; failures only."""
        from app.modules.adminapi import auth_service

        monkeypatch.setattr(auth_service, "LOGIN_IP_MAX_ATTEMPTS", 3)
        await admin_headers(sm, client)
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
        """Rate-limit counts commit in their own transaction and survive a business rollback."""
        from sqlalchemy import select

        from app.core.ratelimit import RateLimitCounter

        for _ in range(3):
            await client.post(
                "/api/v1/auth/login", json={"handle": "u13800000078@test.local", "password": "x"}
            )
        async with sm() as session:
            rows = (
                (
                    await session.execute(
                        select(RateLimitCounter).where(
                            RateLimitCounter.key.like("user-login:%u13800000078@test.local")
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert [r.hits for r in rows] == [3]


class TestAccountLevelLock:
    """Account-level lockout works across IPs."""

    async def test_user_login_account_lock_across_ips(self, client: AsyncClient, sm):
        """10 different IPs fail once each, the 11th attempt is locked by the account bucket."""
        import pytest

        from app.core.errors import AppError, ErrorCode
        from app.modules.account import service as account_service

        await register(client, "u13800000081@test.local", password="secret123456")
        for i in range(10):
            async with sm() as session:
                with pytest.raises(AppError) as exc_info:
                    await account_service.login(
                        session,
                        Handle("email", "u13800000081@test.local"),
                        None,
                        "wrong-pass",
                        client_ip=f"10.0.0.{i}",
                    )
                assert exc_info.value.code is ErrorCode.LOGIN_FAILED
        async with sm() as session:
            with pytest.raises(AppError) as exc_info:
                await account_service.login(
                    session,
                    Handle("email", "u13800000081@test.local"),
                    None,
                    "wrong-pass",
                    client_ip="10.0.0.99",
                )
            assert exc_info.value.code is ErrorCode.RATE_LIMITED
            assert exc_info.value.http_status == 429

    async def test_admin_login_account_lock_across_ips(self, client: AsyncClient, sm):
        """Admin variant: changing IP does not escape the account ladder."""
        import pytest

        from app.core.errors import AppError, ErrorCode
        from app.modules.adminapi import auth_service

        await admin_headers(sm, client)
        for i in range(10):
            async with sm() as session:
                with pytest.raises(AppError) as exc_info:
                    await auth_service.login(
                        session, "admin-user", "wrong", client_ip=f"10.1.0.{i}"
                    )
                assert exc_info.value.code is ErrorCode.LOGIN_FAILED
        async with sm() as session:
            with pytest.raises(AppError) as exc_info:
                await auth_service.login(session, "admin-user", "wrong", client_ip="10.1.0.99")
            assert exc_info.value.code is ErrorCode.RATE_LIMITED

    async def test_success_after_foreign_failures_notifies_and_clears(
        self, client: AsyncClient, sm
    ):
        """Account bucket has failures and the owner signs in: the anomaly notification is sent and
        the bucket reset."""
        import pytest
        from sqlalchemy import select

        from app.core.errors import AppError
        from app.core.ratelimit import read_hits
        from app.modules.account import service as account_service
        from app.modules.notify.models import Notification

        data = await register(client, "u13800000082@test.local", password="secret123456")
        for ip in ("10.2.0.1", "10.2.0.2"):
            async with sm() as session:
                with pytest.raises(AppError):
                    await account_service.login(
                        session,
                        Handle("email", "u13800000082@test.local"),
                        None,
                        "wrong-pass",
                        client_ip=ip,
                    )
        assert await read_hits("user-login-acct:u13800000082@test.local", window_seconds=900.0) == 2
        async with sm() as session:
            pair = await account_service.login(
                session,
                Handle("email", "u13800000082@test.local"),
                None,
                "secret123456",
                client_ip="10.2.0.3",
            )
        assert pair.access_token
        async with sm() as session:
            row = (
                await session.execute(
                    select(Notification).where(Notification.user_id == data["user"]["id"])
                )
            ).scalar_one()
            assert "sign-in attempts" in row.title
            assert row.severity == "warning"
        assert await read_hits("user-login-acct:u13800000082@test.local", window_seconds=900.0) == 0


class TestEnvironmentFailClosed:
    def test_environment_is_required(self, monkeypatch):
        """A missing SUPERDL_ENVIRONMENT refuses to start."""
        import pytest
        from pydantic import ValidationError

        from app.core.config import Settings

        monkeypatch.delenv("SUPERDL_ENVIRONMENT", raising=False)
        with pytest.raises(ValidationError, match="environment"):
            Settings(_env_file=None)  # type: ignore[call-arg]


class TestProdConfigValidation:
    @staticmethod
    def _complete_prod_kwargs() -> dict:
        """The smallest configuration that passes the prod check (provider selection and
        infrastructure items only)."""
        return {
            "_env_file": None,
            "environment": "prod",
            "compliance_profile": "none",
            "jwt_secret": "9f4a1c7e2b8d0f63a5e9c417b3d68f02a1c4e7958b0d326f7a9c1e4b58d2f603",
            "sms_provider": "aliyun",
            "email_provider": "smtp",
            "k8s_backend": "real",
            "payment_mock": False,
            "database_url": "postgresql+asyncpg://svc:strongpass@pg.internal:5432/superdl?sslmode=require",
            "cors_origins": ["https://console.superdl.cn"],
            "admin_host": "admin.superdl.cn",
            "admin_edge_token": "edge-token-for-tests",
            "jupyter_domain_suffix": "app.superdl.cn",
            "service_domain_suffix": "svc.superdl.cn",
            "public_base_url": "https://api.superdl.cn",
            "web_base_url": "https://console.superdl.cn",
            "metrics_token": "mtoken",
            "config_encryption_key": base64.urlsafe_b64encode(b"k" * 32).decode(),
            "image_allowed_registries": "registry.superdl.internal/",
            "bcrypt_rounds": 12,
        }

    def test_prod_rejects_dev_defaults(self):
        """environment=prod + any development default → refused at boot."""
        import pytest
        from pydantic import ValidationError

        from app.core.config import Settings

        with pytest.raises(ValidationError) as ei:
            Settings(
                _env_file=None,  # pyright: ignore[reportCallIssue]
                environment="prod",
                database_url="postgresql+asyncpg://superdl:superdl@localhost:5432/superdl",
            )
        msg = str(ei.value)
        for keyword in ("jwt_secret", "sms_provider", "k8s_backend", "payment_mock"):
            assert keyword in msg

    def test_prod_rejects_placeholder_and_low_entropy_jwt_secret(self):
        """Placeholder / low-entropy JWT secrets are refused in prod."""
        import pytest
        from pydantic import ValidationError

        from app.core.config import Settings

        for bad in (
            "CHANGE_ME_32_CHARS_MINIMUM_______",
            "CHANGE_ME",
            "x" * 40,
        ):
            kwargs = {**self._complete_prod_kwargs(), "jwt_secret": bad}
            with pytest.raises(ValidationError, match="jwt_secret"):
                Settings(**kwargs)

    def test_prod_rejects_mock_email_provider(self):
        """email_provider=mock in prod refuses to boot (fixed verification code)."""
        import pytest
        from pydantic import ValidationError

        from app.core.config import Settings

        with pytest.raises(ValidationError, match="email_provider"):
            Settings(**{**self._complete_prod_kwargs(), "email_provider": "mock"})

    def test_prod_requires_explicit_compliance_profile(self):
        """prod without SUPERDL_COMPLIANCE_PROFILE refuses to boot; any explicit value passes."""
        import pytest
        from pydantic import ValidationError

        from app.core.config import Settings

        kwargs = self._complete_prod_kwargs()
        kwargs.pop("compliance_profile")
        with pytest.raises(ValidationError, match="compliance_profile"):
            Settings(**kwargs)
        assert Settings(**{**kwargs, "compliance_profile": "cn"}).compliance_profile == "cn"

    def test_deployment_identity_shape_checked_in_any_env(self):
        """Unsupported currency or a non-IANA timezone is rejected even outside prod."""
        import pytest
        from pydantic import ValidationError

        from app.core.config import Settings

        base = {"_env_file": None, "environment": "test"}
        with pytest.raises(ValidationError, match="platform_currency"):
            Settings(**base, platform_currency="XXX")
        with pytest.raises(ValidationError, match="billing_timezone"):
            Settings(**base, billing_timezone="Mars/Olympus")
        s = Settings(**base, platform_currency="CNY", billing_timezone="Asia/Shanghai")
        assert (s.platform_currency, s.billing_timezone) == ("CNY", "Asia/Shanghai")

    def test_prod_rejects_weak_bcrypt_cost(self):
        """bcrypt cost below 12 is refused in prod; non-prod allows 4."""
        import pytest
        from pydantic import ValidationError

        from app.core.config import Settings

        with pytest.raises(ValidationError, match="bcrypt_rounds"):
            Settings(**{**self._complete_prod_kwargs(), "bcrypt_rounds": 11})
        fast = {"_env_file": None, "environment": "test", "bcrypt_rounds": 4}
        assert Settings(**fast).bcrypt_rounds == 4

    def test_prod_worker_role_skips_api_only_secrets(self):
        """The worker role skips the jwt_secret and admin_edge_token checks;
        the api role still refuses without admin_edge_token."""
        import pytest
        from pydantic import ValidationError

        from app.core.config import Settings

        worker = {**self._complete_prod_kwargs(), "process_role": "worker"}
        worker.pop("jwt_secret", None)
        worker.pop("admin_edge_token", None)
        assert Settings(**worker).process_role == "worker"
        with pytest.raises(ValidationError, match="sms_provider"):
            Settings(**{**worker, "sms_provider": "mock"})
        tenant_mgr = {
            **worker,
            "worker_component": "tenant-mgr",
            "sms_provider": "mock",
            "payment_mock": True,
        }
        assert Settings(**tenant_mgr).worker_component == "tenant-mgr"
        with pytest.raises(ValidationError, match="k8s_backend"):
            Settings(**{**tenant_mgr, "k8s_backend": "fake"})
        assert Settings(
            **{**tenant_mgr, "worker_component": "disk-ops", "config_encryption_key": None}
        )
        with pytest.raises(ValidationError, match="config_encryption_key"):
            Settings(**{**tenant_mgr, "config_encryption_key": None})
        with pytest.raises(ValidationError, match="admin_edge_token"):
            Settings(**{**self._complete_prod_kwargs(), "admin_edge_token": ""})

    def test_required_real_name_without_enabled_rejected_in_any_env(self):
        """KYC required for top-ups while KYC is off: refused in every environment (the online
        write is stopped by platform_config)."""
        import pytest
        from pydantic import ValidationError

        from app.core.config import Settings

        kwargs = self._complete_prod_kwargs()
        kwargs["real_name_required_for_recharge"] = True
        with pytest.raises(ValidationError, match="real_name_enabled"):
            Settings(**kwargs)
        with pytest.raises(ValidationError, match="real_name_enabled"):
            Settings(
                _env_file=None,  # pyright: ignore[reportCallIssue]
                environment="dev",
                real_name_required_for_recharge=True,
            )
        kwargs["real_name_enabled"] = True
        assert Settings(**kwargs).real_name_required_for_recharge is True

    def test_prod_alipay_enabled_requires_seller_id(self):
        """Alipay enabled in prod without the payee PID refuses to start."""
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
        """CORS wildcard + allow_credentials=true is refused in every environment."""
        import pytest
        from pydantic import ValidationError

        from app.core.config import Settings

        with pytest.raises(ValidationError, match="cors_origins"):
            Settings(
                _env_file=None,  # pyright: ignore[reportCallIssue]
                environment="dev",
                cors_origins=["*"],
            )

    def test_prod_nonlocal_db_requires_tls(self):
        """prod with a non-local PG without sslmode=require refuses to start."""
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
        kwargs["database_url"] = "postgresql+asyncpg://svc:strongpass@127.0.0.1:5432/superdl"
        assert Settings(**kwargs).environment == "prod"


class TestDbTlsTranslate:
    """db._split_db_tls: libpq sslmode in the URL translated into asyncpg parameters."""

    def test_sslmode_translated_to_ssl_connect_arg(self):
        from app.core.db import _split_db_tls

        url, args = _split_db_tls("postgresql+asyncpg://u:p@h:5432/d?sslmode=require")
        assert args == {"ssl": "require"}
        assert "sslmode" not in url

    def test_no_sslmode_passthrough(self):
        from app.core.db import _split_db_tls

        url = "postgresql+asyncpg://u:p@localhost:5432/d"
        assert _split_db_tls(url) == (url, {})

    def test_sslrootcert_builds_verifying_context(self, tmp_path):
        import ssl

        from app.core.db import _split_db_tls

        ca = tmp_path / "ca.crt"
        ca.write_text(_SELF_SIGNED_CA_PEM)
        url, args = _split_db_tls(
            f"postgresql+asyncpg://u:p@h:5432/d?sslmode=verify-full&sslrootcert={ca}"
        )
        assert "sslrootcert" not in url and "sslmode" not in url
        ctx = args["ssl"]
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.check_hostname is True and ctx.verify_mode == ssl.CERT_REQUIRED
        _, args_ca = _split_db_tls(
            f"postgresql+asyncpg://u:p@h:5432/d?sslmode=verify-ca&sslrootcert={ca}"
        )
        assert args_ca["ssl"].check_hostname is False

    def test_sslrootcert_missing_file_or_mode_rejected(self, tmp_path):
        import pytest

        from app.core.db import _split_db_tls

        missing = tmp_path / "nope.crt"
        with pytest.raises(FileNotFoundError):
            _split_db_tls(
                f"postgresql+asyncpg://u:p@h:5432/d?sslmode=verify-full&sslrootcert={missing}"
            )
        with pytest.raises(ValueError):
            _split_db_tls("postgresql+asyncpg://u:p@h:5432/d?sslrootcert=/x.crt")
        with pytest.raises(ValueError):
            _split_db_tls("postgresql+asyncpg://u:p@h:5432/d?sslmode=disable&sslrootcert=/x.crt")


_SELF_SIGNED_CA_PEM = """-----BEGIN CERTIFICATE-----
MIIBNTCB3KADAgECAgEBMAoGCCqGSM49BAMCMBoxGDAWBgNVBAMMD3N1cGVyZGwt
dGVzdC1jYTAeFw0yNjAxMDEwMDAwMDBaFw00NjAxMDEwMDAwMDBaMBoxGDAWBgNV
BAMMD3N1cGVyZGwtdGVzdC1jYTBZMBMGByqGSM49AgEGCCqGSM49AwEHA0IABCor
Pniz2MHG9QNcaxiIULX4rcd/ehKn+oqNzT+f8fn+QBYgdrbugUP6GcPG01VlVe/l
Cl4YZQWGNBDgWWdcVtajEzARMA8GA1UdEwEB/wQFMAMBAf8wCgYIKoZIzj0EAwID
SAAwRQIhAIsr0B/4GdQaf+shsLLIAIYqAWAbsC3BgHPlm2r6pUwvAiAL0aLDMcSx
ci/CD07b8GiOmkvrCRzH8hYgmTyYF+XT9g==
-----END CERTIFICATE-----
"""


class TestSmsCodeBruteForce:
    async def test_code_burned_after_max_attempts(self, client: AsyncClient, sm):
        """A code fails 5 times and is void (used_at stored), the correct code no longer passes."""
        import pytest

        from app.core.errors import AppError
        from app.modules.account.models import VerificationCode

        phone = "u13800000088@test.local"
        resp = await client.post(
            "/api/v1/auth/verification-code",
            json={"handle": phone, "purpose": "register"},
        )
        assert resp.status_code == 204
        for _ in range(5):
            async with sm() as session:
                with pytest.raises(AppError):
                    await verification.consume_code(
                        session, Handle("email", phone), "000000", "register"
                    )
        from sqlalchemy import select

        async with sm() as session:
            row = (
                await session.execute(
                    select(VerificationCode)
                    .where(VerificationCode.target == phone)
                    .order_by(VerificationCode.id.desc())
                )
            ).scalar_one()
        assert row.used_at is not None
        async with sm() as session:
            with pytest.raises(AppError):
                await verification.consume_code(
                    session, Handle("email", phone), "123456", "register"
                )

    async def test_code_is_single_use(self, client: AsyncClient, sm):
        """A consumed code is void at once."""
        import pytest

        from app.core.errors import AppError

        phone = "u13800000090@test.local"
        await client.post(
            "/api/v1/auth/verification-code",
            json={"handle": phone, "purpose": "register"},
        )
        async with sm() as session:
            await verification.consume_code(session, Handle("email", phone), "123456", "register")
            await session.commit()
        async with sm() as session:
            with pytest.raises(AppError):
                await verification.consume_code(
                    session, Handle("email", phone), "123456", "register"
                )

    async def test_code_login_rate_limited(self, client: AsyncClient):
        """Code login (email handle) shares the rate limit with the password path."""
        email = "u13800000089@test.local"
        await register(client, email)
        for _ in range(5):
            resp = await client.post("/api/v1/auth/login", json={"handle": email, "code": "000000"})
            assert resp.json()["code"] == "LOGIN_FAILED"
        resp = await client.post("/api/v1/auth/login", json={"handle": email, "code": "000000"})
        assert resp.status_code == 429
        assert resp.json()["code"] == "RATE_LIMITED"

    async def test_code_send_ip_rate_limited(self, client: AsyncClient):
        """One IP sending codes to many handles at high frequency → 429."""
        for i in range(20):
            resp = await client.post(
                "/api/v1/auth/verification-code",
                json={
                    "handle": f"u138000001{i:02d}@test.local",
                    "purpose": "register",
                },
            )
            assert resp.status_code == 204
        resp = await client.post(
            "/api/v1/auth/verification-code",
            json={"handle": "u13800000199@test.local", "purpose": "register"},
        )
        assert resp.status_code == 429

    async def test_concurrent_same_phone_send_serialized(self, client: AsyncClient, sm):
        """Same-target backoff TOCTOU: two concurrent requests, one passes, the other hits the grown
        backoff 429."""
        import asyncio

        from app.core.errors import AppError, ErrorCode

        async def send() -> None:
            async with sm() as session:
                await verification.send_code(
                    session,
                    Handle("email", "u13800000093@test.local"),
                    "register",
                    client_ip="10.9.0.1",
                )

        results = await asyncio.gather(send(), send(), return_exceptions=True)
        oks = [r for r in results if r is None]
        limited = [
            r for r in results if isinstance(r, AppError) and r.code is ErrorCode.CODE_TOO_FREQUENT
        ]
        assert len(oks) == 1
        assert len(limited) == 1


class TestRequestBodyLimit:
    """Request body hard cap 1MiB (inner layer; the outer one is the Envoy requestBuffer)."""

    async def test_over_limit_declared_content_length_fast_413(self, client: AsyncClient):
        """Content-Length over the cap: 413 without reading the body, unified error body + security
        headers still present."""
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
        """chunked without Content-Length: the streaming count also yields 413."""

        async def stream():
            chunk = b"y" * (256 * 1024)
            for _ in range(5):
                yield chunk

        resp = await client.post(
            "/api/v1/auth/login",
            content=stream(),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 413
        assert resp.json()["code"] == "PAYLOAD_TOO_LARGE"

    async def test_exact_limit_passes_to_router(self, client: AsyncClient):
        """Exactly 1MiB reaches the route (JSON parse failure lands in the 400 family)."""
        resp = await client.post(
            "/api/v1/auth/login",
            content=b"z" * (1024 * 1024),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code != 413
        assert resp.status_code in (400, 422)


class TestSecurityHeaders:
    async def test_headers_on_api_responses(self, client: AsyncClient):
        resp = await client.get("/healthz")
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["x-frame-options"] == "DENY"
        assert "default-src 'none'" in resp.headers["content-security-policy"]
        assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert "camera=()" in resp.headers["permissions-policy"]
        assert resp.headers["cross-origin-opener-policy"] == "same-origin"
        assert resp.headers["cross-origin-resource-policy"] == "same-site"

    async def test_docs_exempt_from_csp(self, client: AsyncClient):
        resp = await client.get("/docs")
        assert "content-security-policy" not in resp.headers

    async def test_api_and_docs_responses_are_no_store(self, client: AsyncClient):
        """API / metrics / docs responses carry no-store, CDNs must not cache; health probes do
        not."""
        for path in ("/api/v1/skus", "/api/v1/no-such-route", "/docs", "/openapi.json"):
            resp = await client.get(path)
            assert resp.headers["cache-control"] == "no-store", path
        assert "cache-control" not in (await client.get("/healthz")).headers


class TestEdgeGuard:
    """prod edge cut-off (always on): /api/admin and /metrics are not exposed on the public api
    domain."""

    async def test_admin_api_hidden_from_public_host(self, client: AsyncClient, monkeypatch):
        from app.core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "environment", "prod", raising=False)
        monkeypatch.setattr(settings, "admin_host", "admin.superdl.cn", raising=False)
        monkeypatch.setattr(settings, "admin_edge_token", "edge-secret-1", raising=False)
        resp = await client.post(
            "/api/admin/v1/auth/login",
            json={"username": "x", "password": "y"},
            headers={"Host": "api.superdl.cn"},
        )
        assert resp.status_code == 404
        resp = await client.post(
            "/api/admin/v1/auth/login",
            json={"username": "x", "password": "y"},
            headers={"Host": "admin.superdl.cn", "X-Admin-Edge-Token": "edge-secret-1"},
        )
        assert resp.status_code == 400
        assert (await client.get("/healthz", headers={"Host": "api.superdl.cn"})).status_code == 200

    async def test_admin_api_rejects_host_match_without_edge_token(
        self, client: AsyncClient, monkeypatch
    ):
        """Two gates: right Host but missing / wrong shared-secret header → 404."""
        from app.core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "environment", "prod", raising=False)
        monkeypatch.setattr(settings, "admin_host", "admin.superdl.cn", raising=False)
        monkeypatch.setattr(settings, "admin_edge_token", "edge-secret-2", raising=False)
        resp = await client.post(
            "/api/admin/v1/auth/login",
            json={"username": "x", "password": "y"},
            headers={"Host": "admin.superdl.cn"},
        )
        assert resp.status_code == 404
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
        assert (
            await client.get("/metrics/", headers={"X-Forwarded-For": "1.2.3.4"})
        ).status_code == 404
        assert (await client.get("/metrics/")).status_code == 401
        assert (
            await client.get("/metrics/", headers={"Authorization": "Bearer mtok"})
        ).status_code == 200


class TestMetricsGuard:
    async def test_non_ascii_authorization_rejected_not_500(self, client: AsyncClient):
        """A non-ASCII Authorization header returns 401."""
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
        """Production exposes neither /docs nor /openapi.json."""
        from app.core.config import get_settings
        from app.main import create_app

        settings = get_settings()
        monkeypatch.setattr(settings, "environment", "prod", raising=False)
        app = create_app()
        assert app.docs_url is None
        assert app.redoc_url is None
        assert app.openapi_url is None
        assert app.openapi()["paths"]


class TestSmsCodeAtRest:
    async def test_code_is_not_stored_in_clear(self, client, sm):
        """No verification-code plaintext in the database."""
        from sqlalchemy import select

        from app.modules.account.models import VerificationCode

        resp = await client.post(
            "/api/v1/auth/verification-code",
            json={"handle": "u13800000777@test.local", "purpose": "register"},
        )
        assert resp.status_code == 204, resp.text
        async with sm() as session:
            row = (
                await session.execute(
                    select(VerificationCode).where(
                        VerificationCode.target == "u13800000777@test.local"
                    )
                )
            ).scalar_one()
        assert "123456" not in row.code_hash
        assert len(row.code_hash) == 64
        from app.core.crypto import hash_verification_code

        target = "u13800000777@test.local"
        assert row.code_hash == hash_verification_code("email", target, "register", "123456")
        assert (
            hash_verification_code("email", "u13800000778@test.local", "register", "123456")
            != row.code_hash
        )
        assert hash_verification_code("email", target, "login", "123456") != row.code_hash
        assert hash_verification_code("sms", target, "register", "123456") != row.code_hash


class TestGhostEnvKeys:
    def test_unknown_superdl_vars_reported(self, monkeypatch):
        """The boot scan catches misspelled / leftover SUPERDL_* variables."""
        from app.core.config import unknown_superdl_env_keys

        monkeypatch.setenv("SUPERDL_JWT_SECRET", "x")
        monkeypatch.setenv("SUPERDL_JWT_SECERT", "typo")
        monkeypatch.setenv("SUPERDL_OLD_REMOVED_KEY", "y")
        unknown = unknown_superdl_env_keys()
        assert "SUPERDL_JWT_SECERT" in unknown
        assert "SUPERDL_OLD_REMOVED_KEY" in unknown
        assert "SUPERDL_JWT_SECRET" not in unknown


class TestBootstrapAdminGate:
    async def test_bootstrap_password_policy_at_service_layer(self, sm):
        """Bootstrap password self-check: too short and over 72 bytes are both refused."""
        import pytest
        from sqlalchemy import select

        from app.modules.adminapi.auth_service import ensure_bootstrap_admin
        from app.modules.adminapi.models import AdminUser

        async with sm() as session:
            with pytest.raises(RuntimeError, match="bootstrap password"):
                await ensure_bootstrap_admin(session, "short")
        async with sm() as session:
            with pytest.raises(RuntimeError, match="bootstrap password"):
                await ensure_bootstrap_admin(session, "汉" * 25)  # 3 bytes per character  # cjk-ok
        async with sm() as session:
            await ensure_bootstrap_admin(session, "l0ng-enough-pass")
            admins = (await session.execute(select(AdminUser))).scalars().all()
        assert [a.username for a in admins] == ["admin"]


class TestUnifiedErrorBodyForHttpException:
    async def test_404_returns_unified_body(self, client: AsyncClient):
        """Route-level 404 is the unified error body too."""
        resp = await client.get("/api/v1/no-such-route")
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "NOT_FOUND"
        assert body["message_key"] == "common.notFound"
        assert body["message"] == "Resource not found"

    async def test_405_returns_unified_body_and_allow_header(self, client: AsyncClient):
        resp = await client.post("/healthz")
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
        data = await register(client, "u13900000071@test.local")
        cross = await client.post(
            "/api/admin/v1/auth/refresh", json={"access_token": data["access_token"]}
        )
        assert cross.status_code == 401

    async def test_renew_grace_and_absolute_cap(self, client: AsyncClient, sm):
        """Renewable within the 15-minute grace after expiry; a first login older than 12 h
        (sess_iat) must sign in again."""
        from datetime import timedelta

        from app.core.security import create_token
        from app.core.timeutil import now_utc
        from app.modules.adminapi.auth_service import SESSION_MAX_SECONDS, create_admin

        async with sm() as session:
            admin = await create_admin(session, "grace-admin", "pass1234", "ops")
        ver = admin.token_version
        expired = create_token(
            str(admin.id),
            "admin",
            extra={"ver": ver},
            iat=now_utc() - timedelta(seconds=3600 + 300),
        )
        resp = await client.post("/api/admin/v1/auth/refresh", json={"access_token": expired})
        assert resp.status_code == 200
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
        async with sm() as session:
            admin.token_version += 1
            session.add(admin)
            await session.commit()
        resp3 = await client.post("/api/admin/v1/auth/refresh", json={"access_token": expired})
        assert resp3.status_code == 401


class TestAuditGate:
    async def test_consecutive_audit_failures_fail_closed(self, client: AsyncClient, monkeypatch):
        """Sustained audit write failures past the threshold → writes fail closed with 503; reads
        unaffected; a successful probe resets."""
        from app.core import audit as audit_mod

        class _Boom:
            def __call__(self) -> object:
                raise RuntimeError("audit db down (injected)")

        monkeypatch.setattr(audit_mod, "get_sessionmaker", _Boom())
        for i in range(audit_mod.AUDIT_FAIL_CLOSED_THRESHOLD):
            resp = await client.post(
                "/api/v1/auth/login", json={"handle": f"u138{i:08d}@test.local", "password": "x"}
            )
            assert resp.status_code == 400
        resp = await client.post("/api/v1/auth/login", json={"handle": "u13800000000@test.local"})
        assert resp.status_code == 503
        assert resp.json()["code"] == "AUDIT_UNAVAILABLE"
        assert (await client.get("/api/v1/auth/captcha-config")).status_code == 200
        assert (await client.get("/healthz")).status_code == 200
        monkeypatch.undo()
        resp = await client.post("/api/v1/auth/login", json={"handle": "u13800000000@test.local"})
        assert resp.status_code == 400


class TestAdminLogout:
    async def test_logout_revokes_all_sessions(self, client: AsyncClient, sm):
        """Server-side logout: token_version+1, issued tokens are invalid at once (this session
        included)."""
        ah = await admin_headers(sm, client, role="ops")
        assert (await client.get("/api/admin/v1/me", headers=ah)).status_code == 200
        resp = await client.post("/api/admin/v1/auth/logout", headers=ah)
        assert resp.status_code == 204
        assert (await client.get("/api/admin/v1/me", headers=ah)).status_code == 401


class TestAuthenticateHeader:
    async def test_401_carries_www_authenticate(self, client: AsyncClient):
        """Failed Bearer auth returns WWW-Authenticate (RFC 6750)."""
        resp = await client.get("/api/v1/notifications")
        assert resp.status_code == 401
        assert resp.headers["www-authenticate"] == "Bearer"
        assert resp.json()["code"] == "UNAUTHORIZED"


class TestAuditOnUnhandledException:
    async def test_500_is_audited(self, sm):
        """Uncaught exceptions (result=500) land in the audit too."""
        from httpx import ASGITransport
        from sqlalchemy import select

        from app.core.audit import AuditLog
        from app.main import create_app

        app = create_app()

        @app.post("/api/v1/__boom", include_in_schema=False)
        async def _boom() -> None:
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
