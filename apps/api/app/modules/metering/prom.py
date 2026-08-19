"""Prometheus 查询客户端。查询模板集中于此,按租户 namespace 注入,禁止任意 PromQL。

标签名与 dcgm-exporter / kube-prometheus-stack 默认配置对齐,实机部署时如有出入只改这里。
"""

from typing import Any

import httpx

from app.core.config import get_settings

# 查询模板:{ns}=租户 namespace,{pod}=实例 uuid
QUERIES = {
    "gpu_util": 'avg(DCGM_FI_DEV_GPU_UTIL{{namespace="{ns}",pod="{pod}"}})',
    "vram_used_mb": 'sum(DCGM_FI_DEV_FB_USED{{namespace="{ns}",pod="{pod}"}})',
    "cpu_pct": (
        'sum(rate(container_cpu_usage_seconds_total{{namespace="{ns}",pod="{pod}",'
        'container!=""}}[2m])) * 100'
    ),
    "mem_used_mb": (
        'sum(container_memory_working_set_bytes{{namespace="{ns}",pod="{pod}",'
        'container!=""}}) / 1024 / 1024'
    ),
}

RANGE_STEPS = {"1h": "60s", "6h": "300s", "24h": "1200s"}

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=get_settings().prometheus_url, timeout=5.0)
    return _client


def set_client(client: httpx.AsyncClient | None) -> None:
    """测试注入(MockTransport)。"""
    global _client
    _client = client


class PrometheusUnavailable(Exception):
    pass


async def query_range(
    metric: str, ns: str, pod: str, *, start: float, end: float, step: str
) -> list[tuple[float, float]]:
    """返回 [(unix_ts, value)];Prometheus 不可用抛 PrometheusUnavailable。"""
    promql = QUERIES[metric].format(ns=ns, pod=pod)
    try:
        resp = await get_client().get(
            "/api/v1/query_range",
            params={"query": promql, "start": start, "end": end, "step": step},
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PrometheusUnavailable(str(exc)) from exc
    if data.get("status") != "success":
        raise PrometheusUnavailable(str(data))
    results = data["data"]["result"]
    if not results:
        return []
    return [(float(ts), float(v)) for ts, v in results[0]["values"]]
