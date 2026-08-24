"""Prometheus 查询客户端。查询模板集中于此,按租户 namespace 注入,禁止任意 PromQL。

标签名与 dcgm-exporter / kube-prometheus-stack 默认配置对齐,实机部署时如有出入只改这里。
"""

from typing import Any

import httpx

from app.core.config import get_settings

# ---- 标签常量:与 dcgm-exporter / HAMi vGPUmonitor 实机形态对齐,如有出入只改这里 ----
DCGM_NODE_LABEL = "Hostname"  # dcgm-exporter 的节点标签
DCGM_GPU_LABEL = "gpu"  # dcgm-exporter 的卡序号标签
HAMI_NS_LABEL = "podnamespace"  # HAMi vGPUmonitor 容器维标签
HAMI_POD_LABEL = "podname"

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

# 共享档实例级:HAMi 软切分下 DCGM 的 per-pod 归属不可靠,改用 vGPUmonitor 容器维指标
# (指标名为 HAMi 默认)。查空时调用方回落 DCGM 模板。
HAMI_QUERIES = {
    "gpu_util": (
        'sum(Device_utilization_desc_of_container{{podnamespace="{ns}",podname="{pod}"}})'
    ),
    "vram_used_mb": (
        'sum(vGPU_device_memory_usage_in_bytes{{podnamespace="{ns}",podname="{pod}"}})'
        " / 1024 / 1024"
    ),
}

# 管理端节点级模板:{node} 注入;不聚合,按卡多序列返回(query_range_multi)
NODE_QUERIES = {
    "util": 'DCGM_FI_DEV_GPU_UTIL{{Hostname="{node}"}}',
    "mem_used_mb": 'DCGM_FI_DEV_FB_USED{{Hostname="{node}"}}',
    "temp": 'DCGM_FI_DEV_GPU_TEMP{{Hostname="{node}"}}',
}
NODE_XID_QUERY = 'sum(increase(DCGM_FI_DEV_XID_ERRORS{{Hostname="{node}"}}[24h]))'

RANGE_STEPS = {"1h": "60s", "6h": "300s", "24h": "1200s"}

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        settings = get_settings()
        _client = httpx.AsyncClient(
            base_url=settings.prometheus_url, timeout=settings.prometheus_timeout_seconds
        )
    return _client


def set_client(client: httpx.AsyncClient | None) -> None:
    """测试注入(MockTransport)。"""
    global _client
    _client = client


async def close_client() -> None:
    """lifespan 收尾调用:显式关闭全局客户端连接池,不放任 socket 泄漏。"""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


class PrometheusUnavailable(Exception):
    pass


def _extract_result(data: dict[str, Any]) -> list[dict[str, Any]]:
    """校验并取出 data.result;响应形态异常(缺键/类型错)统一归 PrometheusUnavailable,
    不能让 KeyError 击穿成 500 —— 上游抖动对调用方必须是 503/降级语义。"""
    if data.get("status") != "success":
        raise PrometheusUnavailable(str(data))
    try:
        return list(data["data"]["result"])
    except (KeyError, TypeError) as exc:
        raise PrometheusUnavailable(f"malformed prometheus response: {exc}") from exc


def _extract_points(series: dict[str, Any]) -> list[tuple[float, float]]:
    """取单序列 [(unix_ts, value)];形态异常归 PrometheusUnavailable。"""
    try:
        return [(float(ts), float(v)) for ts, v in series["values"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise PrometheusUnavailable(f"malformed prometheus series: {exc}") from exc


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
    results = _extract_result(data)
    if not results:
        return []
    return _extract_points(results[0])


async def query_range_raw(
    promql: str, *, start: float, end: float, step: str
) -> list[dict[str, Any]]:
    """query_range 原始 result 列表(白名单模板已格式化后传入)。"""
    try:
        resp = await get_client().get(
            "/api/v1/query_range",
            params={"query": promql, "start": start, "end": end, "step": step},
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PrometheusUnavailable(str(exc)) from exc
    return _extract_result(data)


async def query_range_multi(
    promql: str, *, start: float, end: float, step: str, group_label: str = DCGM_GPU_LABEL
) -> list[tuple[str, list[tuple[float, float]]]]:
    """多序列 query_range:按 group_label 分组返回 [(标签值, [(ts, v)])],标签值升序。"""
    results = await query_range_raw(promql, start=start, end=end, step=step)
    out: list[tuple[str, list[tuple[float, float]]]] = []
    for r in results:
        label = str(r.get("metric", {}).get(group_label, ""))
        out.append((label, [(float(ts), float(v)) for ts, v in r.get("values", [])]))
    out.sort(key=lambda x: (len(x[0]), x[0]))  # "2" < "10" 的自然序
    return out


async def query_instant(promql: str) -> float | None:
    """瞬时查询取首序列标量值;无数据返回 None。"""
    try:
        resp = await get_client().get("/api/v1/query", params={"query": promql})
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PrometheusUnavailable(str(exc)) from exc
    results = _extract_result(data)
    if not results:
        return None
    try:
        return float(results[0]["value"][1])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise PrometheusUnavailable(f"malformed prometheus response: {exc}") from exc


async def query_instance_metric(
    metric: str, ns: str, pod: str, *, tier: str | None, start: float, end: float, step: str
) -> list[tuple[float, float]]:
    """实例级单指标:shared 档 gpu_util/vram 优先 HAMi 容器维指标,查空回落 DCGM。"""
    if tier in ("shared_std", "shared_eco") and metric in HAMI_QUERIES:
        promql = HAMI_QUERIES[metric].format(ns=ns, pod=pod)
        results = await query_range_raw(promql, start=start, end=end, step=step)
        if results:
            return _extract_points(results[0])
    return await query_range(metric, ns, pod, start=start, end=end, step=step)
