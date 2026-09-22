"""Prometheus usage proxy, aggregation, reconciliation and failure degradation."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.modules.metering import prom
from app.modules.metering.models import UsageHourly
from app.modules.metering.service import aggregate_previous_hour
from tests.helpers import prom_mock, provision_running, register

pytestmark = pytest.mark.usefixtures("fake")


class TestMetricsProxy:
    async def test_metrics_endpoint(self, client, sm, fake):
        headers, uuid, _user_id = await provision_running(client, sm, fake)
        prom.set_client(prom_mock([(1e9, 55.0), (1e9 + 60, 60.0)]))
        resp = await client.get(
            f"/api/v1/instances/{uuid}/metrics", params={"range": "1h"}, headers=headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["series"]["gpu_util"] == [[1e9, 55.0], [1e9 + 60, 60.0]]

    async def test_prometheus_down_degrades_gracefully(self, client, sm, fake):
        headers, uuid, _user_id = await provision_running(client, sm, fake)
        prom.set_client(prom_mock(fail=True))
        resp = await client.get(f"/api/v1/instances/{uuid}/metrics", headers=headers)
        assert resp.status_code == 503
        assert resp.json()["message_key"] == "metering.unavailable"

    async def test_cannot_read_others_metrics(self, client, sm, fake):
        _headers, uuid, _user_id = await provision_running(client, sm, fake)
        other = await register(client, "u13600000001@test.local")
        resp = await client.get(
            f"/api/v1/instances/{uuid}/metrics",
            headers={"Authorization": f"Bearer {other['access_token']}"},
        )
        assert resp.status_code == 404


class TestAggregation:
    async def test_aggregate_idempotent(self, client, sm, fake):
        _headers, _uuid, _user_id = await provision_running(client, sm, fake)
        prom.set_client(prom_mock([(1e9, 50.0), (1e9 + 60, 70.0), (1e9 + 120, 90.0)]))
        at = datetime.now(UTC) + timedelta(hours=1)
        assert await aggregate_previous_hour(sm, at=at) == 1
        assert await aggregate_previous_hour(sm, at=at) == 1
        async with sm() as session:
            rows = (await session.execute(select(UsageHourly))).scalars().all()
        assert len(rows) == 1
        assert rows[0].gpu_util_avg == 70.0

    async def test_aggregate_prom_down_no_crash(self, client, sm, fake):
        await provision_running(client, sm, fake)
        prom.set_client(prom_mock(fail=True))
        at = datetime.now(UTC) + timedelta(hours=1)
        assert await aggregate_previous_hour(sm, at=at) == 0


def prom_mock_routed(routes: dict[str, list[dict]], *, default: list[dict] | None = None):
    """Fake Prometheus routing by PromQL substring: routes {substring: result list}."""

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params.get("query", "")
        result = default if default is not None else []
        for needle, res in routes.items():
            if needle in query:
                result = res
                break
        return httpx.Response(
            200, text=json.dumps({"status": "success", "data": {"result": result}})
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://prom")


class TestNodeMetrics:
    async def test_node_gpu_metrics_multi_series(self):
        from app.modules.metering.service import node_gpu_metrics

        series = lambda gpu, v: {"metric": {"gpu": gpu}, "values": [[1.0, str(v)]]}  # noqa: E731
        prom.set_client(
            prom_mock_routed(
                {
                    "DCGM_FI_DEV_GPU_UTIL": [series("0", 78), series("1", 12)],
                    "DCGM_FI_DEV_FB_USED": [series("0", 21000), series("1", 3000)],
                    "DCGM_FI_DEV_GPU_TEMP": [series("0", 64), series("1", 41)],
                    "DCGM_FI_DEV_XID_ERRORS": [{"metric": {}, "value": [1.0, "2"]}],
                }
            )
        )
        out = await node_gpu_metrics("gpu-a3-01", "1h")
        assert out.available is True
        assert [g.index for g in out.gpus] == ["0", "1"]
        assert out.gpus[0].util == [(1.0, 78.0)]
        assert out.xid_count_24h == 2

    async def test_node_gpu_metrics_degrades(self):
        from app.modules.metering.service import node_gpu_metrics

        prom.set_client(prom_mock(fail=True))
        out = await node_gpu_metrics("gpu-a3-01", "1h")
        assert out.model_dump(exclude={"grafana_url"}) == {
            "available": False,
            "range": "1h",
            "gpus": [],
            "xid_count_24h": 0,
        }


class TestTierSource:
    async def test_hami_pool_prefers_hami_and_falls_back(self):
        """hami pool gpu_util prefers the HAMi metric; empty HAMi falls back to DCGM; kata / mig
        always
        DCGM."""
        hami_point = [{"metric": {}, "values": [[1.0, "55"]]}]
        dcgm_point = [{"metric": {}, "values": [[1.0, "70"]]}]
        prom.set_client(
            prom_mock_routed(
                {
                    "hami_container_device_utilization_ratio": hami_point,
                    "DCGM_FI_DEV_GPU_UTIL": dcgm_point,
                }
            )
        )
        hami = await prom.query_instance_metric(
            "gpu_util", "tenant-1", "u1", pool_label="hami", start=0, end=1, step="60s"
        )
        assert hami == [(1.0, 55.0)]
        kata = await prom.query_instance_metric(
            "gpu_util", "tenant-1", "u1", pool_label="kata", start=0, end=1, step="60s"
        )
        assert kata == [(1.0, 70.0)]
        prom.set_client(prom_mock_routed({"DCGM_FI_DEV_GPU_UTIL": dcgm_point}))
        fallback = await prom.query_instance_metric(
            "gpu_util", "tenant-1", "u1", pool_label="hami", start=0, end=1, step="60s"
        )
        assert fallback == [(1.0, 70.0)]


def prom_mock_malformed():
    """A 200 response missing the data key."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=json.dumps({"status": "success"}))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://prom")


class TestMalformedResponse:
    async def test_instance_metrics_503_not_500(self, client, sm, fake):
        """Prometheus response without the data key: 503 degradation."""
        headers, uuid, _user_id = await provision_running(client, sm, fake)
        prom.set_client(prom_mock_malformed())
        resp = await client.get(f"/api/v1/instances/{uuid}/metrics", headers=headers)
        assert resp.status_code == 503
        assert resp.json()["message_key"] == "metering.unavailable"


class TestAggregationPartialFailure:
    async def test_single_failure_does_not_drop_whole_hour(self, client, sm, fake):
        """A failing single-instance query loses only that instance-hour, the rest of the round
        aggregates as usual."""
        _h1, uuid1, _u1 = await provision_running(client, sm, fake, email="u13900000021@test.local")
        _h2, _uuid2, _u2 = await provision_running(
            client, sm, fake, email="u13900000022@test.local"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            query = request.url.params.get("query", "")
            if uuid1 in query:
                return httpx.Response(500, text="flap")
            body = {
                "status": "success",
                "data": {"result": [{"metric": {}, "values": [[1e9, "50"]]}]},
            }
            return httpx.Response(200, text=json.dumps(body))

        prom.set_client(
            httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://p")
        )
        at = datetime.now(UTC) + timedelta(hours=1)
        assert await aggregate_previous_hour(sm, at=at) == 1
        async with sm() as session:
            rows = (await session.execute(select(UsageHourly))).scalars().all()
        assert len(rows) == 1


class TestMissingSampleRegressions:
    async def test_late_samples_fill_null_but_never_replace_valid_data(self, sm, monkeypatch):
        from decimal import Decimal
        from unittest.mock import AsyncMock

        from app.modules.metering import service

        hour = datetime(2026, 1, 1, tzinfo=UTC)
        async with sm() as session:
            session.add_all(
                [
                    UsageHourly(instance_id=1, hour_start=hour, gpu_util_avg=None),
                    UsageHourly(instance_id=2, hour_start=hour, gpu_util_avg=0.0),
                    UsageHourly(instance_id=3, hour_start=hour, gpu_util_avg=30.0),
                ]
            )
            await session.commit()
        monkeypatch.setattr(
            service.orchestrator_queries,
            "billing_candidates",
            AsyncMock(return_value=[(i, 1, Decimal("1.0000"), 1) for i in range(1, 5)]),
        )
        monkeypatch.setattr(
            service.orchestrator_queries,
            "instance_locations",
            AsyncMock(return_value={i: ("tenant-1", str(i), "kata") for i in range(1, 5)}),
        )
        samples = {"1": [(1.0, 0.0)], "2": [(1.0, 90.0)], "3": [], "4": []}

        async def query(_metric, _ns, pod, **_kwargs):
            return samples[pod]

        monkeypatch.setattr(service.prom, "query_instance_metric", query)
        at = hour + timedelta(hours=1)
        assert await aggregate_previous_hour(sm, at=at) == 2
        async with sm() as session:
            rows = (await session.execute(select(UsageHourly))).scalars().all()
            assert {r.instance_id: r.gpu_util_avg for r in rows} == {1: 0.0, 2: 0.0, 3: 30.0}
        samples["4"] = [(1.0, 20.0)]
        assert await aggregate_previous_hour(sm, at=at) == 3
        async with sm() as session:
            rows = (await session.execute(select(UsageHourly))).scalars().all()
            assert {r.instance_id: r.gpu_util_avg for r in rows} == {
                1: 0.0,
                2: 0.0,
                3: 30.0,
                4: 20.0,
            }

    async def test_reconciliation_counts_zero_and_excludes_null(self, sm, monkeypatch):
        from decimal import Decimal
        from unittest.mock import AsyncMock

        from app.modules.metering import service

        day = datetime(2026, 1, 1, tzinfo=UTC)
        async with sm() as session:
            session.add_all(
                [
                    UsageHourly(instance_id=1, hour_start=day, gpu_util_avg=None),
                    UsageHourly(
                        instance_id=1, hour_start=day + timedelta(hours=1), gpu_util_avg=0.0
                    ),
                    UsageHourly(instance_id=2, hour_start=day, gpu_util_avg=None),
                ]
            )
            await session.commit()
        monkeypatch.setattr(
            service.billing_service, "billed_by_instance", AsyncMock(return_value={})
        )
        monkeypatch.setattr(
            service.orchestrator_queries,
            "instance_hourly_prices",
            AsyncMock(return_value={1: Decimal("2.0000"), 2: Decimal("9.0000")}),
        )
        async with sm() as session:
            report = await service.reconciliation_report(session, day)
        assert report.estimated_total == "2.00"
        assert [(row.instance_id, row.estimated) for row in report.outliers] == [(1, "2.00")]


class TestNodeNameValidation:
    async def test_injection_rejected_with_400(self):
        """An invalid node_name → 400, never reaching the PromQL template."""
        from app.core.errors import AppError
        from app.modules.metering.service import node_gpu_metrics

        for bad in ('gpu";drop', "a b", "UPPER_ok", "x" * 300, "-lead", ""):
            with pytest.raises(AppError) as exc_info:
                await node_gpu_metrics(bad, "1h")
            assert exc_info.value.http_status == 400
