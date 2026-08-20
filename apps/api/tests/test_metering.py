"""用量:Prometheus 代理/聚合/对账。验收:Prometheus 停机不影响计费。"""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.core.k8s import set_orchestrator
from app.core.k8s.fake import FakeOrchestrator
from app.modules.metering import prom
from app.modules.metering.models import UsageHourly
from app.modules.metering.service import aggregate_previous_hour
from tests.test_catalog import admin_headers
from tests.test_orchestrator_lifecycle import _provision_running

pytestmark = pytest.mark.usefixtures("fake")


@pytest.fixture
def fake():
    orch = FakeOrchestrator(auto_ready=False)
    set_orchestrator(orch)
    yield orch
    set_orchestrator(None)


def prom_mock(values: list[tuple[float, float]] | None = None, *, fail: bool = False):
    """构造假 Prometheus:MockTransport 注入。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            return httpx.Response(500, text="down")
        body = {
            "status": "success",
            "data": {
                "result": (
                    [{"metric": {}, "values": [[ts, str(v)] for ts, v in values]}] if values else []
                )
            },
        }
        return httpx.Response(200, text=json.dumps(body))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://prom")


@pytest.fixture(autouse=True)
def _reset_prom_client():
    yield
    prom.set_client(None)


class TestMetricsProxy:
    async def test_metrics_endpoint(self, client, sm, fake):
        headers, uuid, _user_id = await _provision_running(client, sm, fake)
        prom.set_client(prom_mock([(1e9, 55.0), (1e9 + 60, 60.0)]))
        resp = await client.get(
            f"/api/v1/instances/{uuid}/metrics", params={"range": "1h"}, headers=headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["series"]["gpu_util"] == [[1e9, 55.0], [1e9 + 60, 60.0]]

    async def test_prometheus_down_degrades_gracefully(self, client, sm, fake):
        headers, uuid, _user_id = await _provision_running(client, sm, fake)
        prom.set_client(prom_mock(fail=True))
        resp = await client.get(f"/api/v1/instances/{uuid}/metrics", headers=headers)
        assert resp.status_code == 503
        assert "不影响计费" in resp.json()["message"]

    async def test_cannot_read_others_metrics(self, client, sm, fake):
        _headers, uuid, _user_id = await _provision_running(client, sm, fake)
        from tests.test_account_auth import register

        other = await register(client, "13600000001")
        resp = await client.get(
            f"/api/v1/instances/{uuid}/metrics",
            headers={"Authorization": f"Bearer {other['access_token']}"},
        )
        assert resp.status_code == 404


class TestAggregation:
    async def test_aggregate_idempotent(self, client, sm, fake):
        _headers, _uuid, _user_id = await _provision_running(client, sm, fake)
        prom.set_client(prom_mock([(1e9, 50.0), (1e9 + 60, 70.0), (1e9 + 120, 90.0)]))
        at = datetime.now(UTC) + timedelta(hours=1)
        assert await aggregate_previous_hour(sm, at=at) == 1
        assert await aggregate_previous_hour(sm, at=at) == 1  # UNIQUE DO NOTHING,不重复插入
        async with sm() as session:
            rows = (await session.execute(select(UsageHourly))).scalars().all()
        assert len(rows) == 1
        assert rows[0].gpu_util_avg == 70.0
        assert rows[0].gpu_util_p95 == 90.0

    async def test_aggregate_prom_down_no_crash(self, client, sm, fake):
        await _provision_running(client, sm, fake)
        prom.set_client(prom_mock(fail=True))
        at = datetime.now(UTC) + timedelta(hours=1)
        assert await aggregate_previous_hour(sm, at=at) == 0  # 静默跳过,计费不受影响


class TestReconciliation:
    async def test_reconciliation_report(self, client, sm, fake):
        from tests.test_billing_flow import backdate_running_event

        headers, uuid, _user_id = await _provision_running(client, sm, fake)
        await backdate_running_event(sm, uuid, 30)
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)  # 尾账产生

        ah = await admin_headers(sm, client, role="finance")
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        resp = await client.get("/api/admin/v1/reconciliation", params={"day": day}, headers=ah)
        assert resp.status_code == 200, resp.text
        report = resp.json()
        assert "billed_total" in report and "diff_pct" in report
