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
        assert resp.json()["message_key"] == "metering.unavailable"

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


def prom_mock_routed(routes: dict[str, list[dict]], *, default: list[dict] | None = None):
    """按 PromQL 子串路由的假 Prometheus:routes {子串: result 列表}。"""

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
        assert out["available"] is True
        assert [g["index"] for g in out["gpus"]] == ["0", "1"]
        assert out["gpus"][0]["util"] == [(1.0, 78.0)]
        assert out["xid_count_24h"] == 2

    async def test_node_gpu_metrics_degrades(self):
        from app.modules.metering.service import node_gpu_metrics

        prom.set_client(prom_mock(fail=True))
        out = await node_gpu_metrics("gpu-a3-01", "1h")
        assert out == {"available": False, "range": "1h", "gpus": [], "xid_count_24h": 0}


class TestTierSource:
    async def test_shared_tier_prefers_hami_and_falls_back(self):
        """shared 档 gpu_util 优先 HAMi 指标;HAMi 查空回落 DCGM;dedicated 恒 DCGM。"""
        hami_point = [{"metric": {}, "values": [[1.0, "55"]]}]
        dcgm_point = [{"metric": {}, "values": [[1.0, "70"]]}]
        prom.set_client(
            prom_mock_routed(
                {
                    "Device_utilization_desc_of_container": hami_point,
                    "DCGM_FI_DEV_GPU_UTIL": dcgm_point,
                }
            )
        )
        shared = await prom.query_instance_metric(
            "gpu_util", "tenant-1", "u1", tier="shared_std", start=0, end=1, step="60s"
        )
        assert shared == [(1.0, 55.0)]
        dedicated = await prom.query_instance_metric(
            "gpu_util", "tenant-1", "u1", tier="dedicated", start=0, end=1, step="60s"
        )
        assert dedicated == [(1.0, 70.0)]
        # HAMi 查空 → 回落 DCGM
        prom.set_client(prom_mock_routed({"DCGM_FI_DEV_GPU_UTIL": dcgm_point}))
        fallback = await prom.query_instance_metric(
            "gpu_util", "tenant-1", "u1", tier="shared_eco", start=0, end=1, step="60s"
        )
        assert fallback == [(1.0, 70.0)]


def prom_mock_malformed():
    """返回 200 但缺 data 键的畸形响应(上游故障/代理截断)。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=json.dumps({"status": "success"}))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://prom")


class TestMalformedResponse:
    async def test_instance_metrics_503_not_500(self, client, sm, fake):
        """Prometheus 响应缺 data 键:KeyError 不能击穿成 500,必须走 503 降级语义。"""
        headers, uuid, _user_id = await _provision_running(client, sm, fake)
        prom.set_client(prom_mock_malformed())
        resp = await client.get(f"/api/v1/instances/{uuid}/metrics", headers=headers)
        assert resp.status_code == 503
        assert resp.json()["message_key"] == "metering.unavailable"

    async def test_node_metrics_degrades_not_crashes(self):
        from app.modules.metering.service import node_gpu_metrics

        prom.set_client(prom_mock_malformed())
        out = await node_gpu_metrics("gpu-a3-01", "1h")
        assert out["available"] is False


class TestAggregationPartialFailure:
    async def test_single_failure_does_not_drop_whole_hour(self, client, sm, fake):
        """单实例查询失败只丢该实例该小时:整轮其它实例照常聚合(此前 return 全丢)。"""
        _h1, uuid1, _u1 = await _provision_running(client, sm, fake, phone="13900000021")
        _h2, _uuid2, _u2 = await _provision_running(client, sm, fake, phone="13900000022")

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
        assert await aggregate_previous_hour(sm, at=at) == 1  # uuid2 正常写入
        async with sm() as session:
            rows = (await session.execute(select(UsageHourly))).scalars().all()
        assert len(rows) == 1

    async def test_vram_zero_is_not_null(self, client, sm, fake):
        """vram 峰值 0 是合法值,不能写成 NULL(此前 `or None` 会吞掉 0)。"""
        await _provision_running(client, sm, fake, phone="13900000023")
        prom.set_client(
            prom_mock_routed(
                {
                    "DCGM_FI_DEV_GPU_UTIL": [{"metric": {}, "values": [[1e9, "50"]]}],
                    "DCGM_FI_DEV_FB_USED": [{"metric": {}, "values": [[1e9, "0"]]}],
                    "container_cpu_usage_seconds_total": [{"metric": {}, "values": [[1e9, "3"]]}],
                }
            )
        )
        at = datetime.now(UTC) + timedelta(hours=1)
        assert await aggregate_previous_hour(sm, at=at) == 1
        async with sm() as session:
            row = (await session.execute(select(UsageHourly))).scalar_one()
        assert row.vram_max_mb == 0
        assert row.gpu_util_avg == 50.0


class TestNodeNameValidation:
    async def test_injection_rejected_with_400(self):
        """node_name 是 format 进 PromQL 的路径参数:非法名必须 400,不能进查询模板。"""
        from app.core.errors import AppError
        from app.modules.metering.service import node_gpu_metrics

        for bad in ('gpu";drop', "a b", "UPPER_ok", "x" * 300, "-lead", ""):
            with pytest.raises(AppError) as exc_info:
                await node_gpu_metrics(bad, "1h")
            assert exc_info.value.http_status == 400
