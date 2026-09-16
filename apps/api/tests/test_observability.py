"""可观测性与运维健壮性:request-id 贯穿、业务指标、异常兜底、健康探针、数据保洁。"""

import re
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text, update

from app.core.observability import request_id_from_header
from app.core.timeutil import now_utc
from tests.helpers import as_handle

_HEX16 = re.compile(r"^[0-9a-f]{16}$")


class TestRequestId:
    async def test_generated_and_echoed(self, client: AsyncClient):
        resp = await client.get("/healthz")
        assert len(resp.headers["x-request-id"]) >= 8

    async def test_wellformed_inbound_id_reused(self, client: AsyncClient):
        resp = await client.get("/healthz", headers={"X-Request-ID": "gw-1.a_B"})
        assert resp.headers["x-request-id"] == "gw-1.a_B"

    @pytest.mark.parametrize("bad", ["has space", "a" * 65, "x\ty", "<script>", ""])
    async def test_malformed_inbound_id_replaced(self, client: AsyncClient, bad: str):
        """不合规则的 X-Request-ID 不沿用:响应头与错误体都是服务端生成的 16 位十六进制。"""
        resp = await client.get("/api/v1/no-such-route", headers={"X-Request-ID": bad})
        rid = resp.headers["x-request-id"]
        assert _HEX16.match(rid) and rid != bad
        assert resp.json()["request_id"] == rid

    def test_request_id_from_header_rules(self):
        assert request_id_from_header("abc-123_x.y") == "abc-123_x.y"
        assert request_id_from_header("a" * 64) == "a" * 64
        assert _HEX16.match(request_id_from_header("a" * 65))
        assert _HEX16.match(request_id_from_header(None))
        assert _HEX16.match(request_id_from_header("bad id"))

    async def test_error_body_carries_request_id(self, client: AsyncClient):
        """错误响应体回带 request_id。"""
        resp = await client.get("/api/v1/no-such-route", headers={"X-Request-ID": "gw-err-1"})
        assert resp.status_code == 404
        assert resp.json()["request_id"] == "gw-err-1"


class TestHealthEndpoints:
    async def test_ready_when_db_up(self, client: AsyncClient):
        resp = await client.get("/readyz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"

    async def test_schema_mismatch_not_ready(self, client: AsyncClient, sm):
        """DB 版本与代码 head 不一致:503。"""
        from app.core.db import code_schema_head

        async with sm() as session:
            await session.execute(text("UPDATE alembic_version SET version_num = '000000000000'"))
            await session.commit()
        try:
            resp = await client.get("/readyz")
            assert resp.status_code == 503
            assert resp.json()["status"] == "schema_mismatch"
        finally:
            async with sm() as session:
                await session.execute(
                    text("UPDATE alembic_version SET version_num = :v"),
                    {"v": code_schema_head()},
                )
                await session.commit()


class TestBusinessMetrics:
    async def test_http_histogram_uses_route_template(self, client: AsyncClient):
        await client.get("/api/v1/skus")
        body = (await client.get("/metrics/")).text
        assert 'route="/api/v1/skus"' in body


class TestUnhandledException:
    async def test_500_keeps_security_headers_and_request_id(self, sm):
        """未捕获异常 → 统一 500 错误体,安全响应头与 x-request-id 仍在(Uniform500)。"""
        from app.main import create_app

        app = create_app()

        @app.get("/boom2", include_in_schema=False)
        async def boom2() -> dict:
            raise RuntimeError("kaboom")

        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/boom2", headers={"X-Request-ID": "gw-boom-1"})
        assert resp.status_code == 500
        assert resp.json()["code"] == "INTERNAL"
        assert "kaboom" not in resp.text
        assert resp.headers["x-request-id"] == "gw-boom-1"
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.json()["request_id"] == "gw-boom-1"


class TestCleanup:
    async def test_expired_rows_removed(self, client: AsyncClient, sm):
        from app.modules.account.models import UsedRefreshToken, VerificationCode
        from app.workers.cleanup import cleanup_expired_rows

        async with sm() as session:
            session.add(
                VerificationCode(
                    channel="email",
                    target=as_handle("13800000150"),
                    code_hash="0" * 64,
                    purpose="register",
                    expires_at=now_utc() - timedelta(days=8),
                )
            )
            session.add(
                UsedRefreshToken(jti="deadbeef" * 4, expires_at=now_utc() - timedelta(hours=1))
            )
            await session.commit()
            await session.execute(
                update(VerificationCode)
                .where(VerificationCode.target == as_handle("13800000150"))
                .values(created_at=now_utc() - timedelta(days=9))
            )
            await session.commit()

        counts = await cleanup_expired_rows(sm)
        assert counts["verification_codes"] >= 1
        assert counts["used_refresh_tokens"] >= 1

        async with sm() as session:
            left = (
                await session.execute(
                    select(VerificationCode).where(
                        VerificationCode.target == as_handle("13800000150")
                    )
                )
            ).scalar_one_or_none()
            assert left is None
            assert (
                await session.execute(text("SELECT count(*) FROM used_refresh_tokens"))
            ).scalar_one() == 0
