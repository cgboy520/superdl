"""可观测性与运维健壮性:request-id 贯穿、业务指标、异常兜底、健康探针、数据保洁。"""

from datetime import timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text, update

from app.core.timeutil import now_utc


class TestRequestId:
    async def test_generated_and_echoed(self, client: AsyncClient):
        resp = await client.get("/healthz")
        assert len(resp.headers["x-request-id"]) >= 8

    async def test_incoming_honored(self, client: AsyncClient):
        resp = await client.get("/healthz", headers={"X-Request-ID": "gw-abc123"})
        assert resp.headers["x-request-id"] == "gw-abc123"

    async def test_cors_exposes_request_id(self, client: AsyncClient):
        """浏览器跨域 fetch 默认读不到自定义响应头:必须经 expose_headers 放行。"""
        resp = await client.get("/healthz", headers={"Origin": "http://localhost:5173"})
        assert "x-request-id" in resp.headers.get("access-control-expose-headers", "").lower()

    async def test_error_body_carries_request_id(self, client: AsyncClient):
        """错误响应体回带 request_id:用户报错时凭单号即可串联日志。"""
        resp = await client.get("/api/v1/no-such-route", headers={"X-Request-ID": "gw-err-1"})
        assert resp.status_code == 404
        assert resp.json()["request_id"] == "gw-err-1"


class TestHealthEndpoints:
    async def test_ready_when_db_up(self, client: AsyncClient):
        resp = await client.get("/readyz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"


class TestBusinessMetrics:
    async def test_http_histogram_uses_route_template(self, client: AsyncClient):
        await client.get("/api/v1/skus")
        body = (await client.get("/metrics/")).text
        assert 'route="/api/v1/skus"' in body


class TestUnhandledException:
    async def test_uniform_500_body(self, sm):
        from app.main import create_app

        app = create_app()

        @app.get("/boom", include_in_schema=False)
        async def boom() -> dict:  # pyright: ignore[reportUnusedFunction]
            raise RuntimeError("kaboom")

        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/boom")
        assert resp.status_code == 500
        assert resp.json()["code"] == "INTERNAL"
        assert "kaboom" not in resp.text  # 不泄露内部细节


class TestCleanup:
    async def test_expired_rows_removed(self, client: AsyncClient, sm):
        from app.modules.account.models import SmsCode, UsedRefreshToken
        from app.workers.main import cleanup_expired_rows

        async with sm() as session:
            session.add(
                SmsCode(
                    phone="13800000150",
                    code_hash="0" * 64,
                    purpose="register",
                    expires_at=now_utc() - timedelta(days=8),
                )
            )
            session.add(
                UsedRefreshToken(
                    jti="deadbeef" * 4, user_id=1, expires_at=now_utc() - timedelta(hours=1)
                )
            )
            await session.commit()
            # created_at 由 server_default 生成,需回拨越过 7 天窗口
            await session.execute(
                update(SmsCode)
                .where(SmsCode.phone == "13800000150")
                .values(created_at=now_utc() - timedelta(days=9))
            )
            await session.commit()

        counts = await cleanup_expired_rows(sm)
        assert counts["sms_codes"] >= 1
        assert counts["used_refresh_tokens"] >= 1

        async with sm() as session:
            left = (
                await session.execute(select(SmsCode).where(SmsCode.phone == "13800000150"))
            ).scalar_one_or_none()
            assert left is None
            assert (
                await session.execute(text("SELECT count(*) FROM used_refresh_tokens"))
            ).scalar_one() == 0
