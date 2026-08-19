"""可观测性与运维健壮性:request-id 贯穿、业务指标、异常兜底、readyz、数据保洁。"""

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


class TestReadyz:
    async def test_ready_when_db_up(self, client: AsyncClient):
        resp = await client.get("/readyz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"


class TestBusinessMetrics:
    async def test_payment_mismatch_counted(self, client: AsyncClient, sm):
        from tests.test_payment import create_order, pay_mock, user_headers

        headers = await user_headers(client, "13700000041")
        order = await create_order(client, headers, "40.00")
        resp = await pay_mock(client, order["order_no"], "39.99")  # 金额不符
        assert resp.status_code == 400

        body = (await client.get("/metrics/")).text
        assert "superdl_payment_callback_mismatch_total" in body
        assert "superdl_http_request_duration_seconds" in body

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
                    code="123456",
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
