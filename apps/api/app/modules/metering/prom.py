"""Prometheus query client: query templates live here, injected per tenant namespace, arbitrary
PromQL is never accepted."""

from typing import Any

import httpx

from app.core.config import get_settings
from app.core.gpu_adapter import POOL_HAMI

DCGM_NODE_LABEL = "hostname"
DCGM_GPU_LABEL = "gpu"
HAMI_NS_LABEL = "namespace"
HAMI_POD_LABEL = "pod"

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

HAMI_QUERIES = {
    "gpu_util": (
        "sum(hami_container_device_utilization_ratio"
        f'{{{{{HAMI_NS_LABEL}="{{ns}}",{HAMI_POD_LABEL}="{{pod}}"}}}})'
    ),
    "vram_used_mb": (
        f'sum(hami_vgpu_memory_used_bytes{{{{{HAMI_NS_LABEL}="{{ns}}",{HAMI_POD_LABEL}="{{pod}}"}}}})'
        " / 1024 / 1024"
    ),
}

_NODE_SEL = f'{{{{{DCGM_NODE_LABEL}="{{node}}"}}}}'
NODE_QUERIES = {
    "util": f"DCGM_FI_DEV_GPU_UTIL{_NODE_SEL}",
    "mem_used_mb": f"DCGM_FI_DEV_FB_USED{_NODE_SEL}",
    "temp": f"DCGM_FI_DEV_GPU_TEMP{_NODE_SEL}",
}
NODE_XID_QUERY = f"sum(changes(DCGM_FI_DEV_XID_ERRORS{_NODE_SEL}[24h]))"

COMPONENT_QUERIES = {
    "dcgm_sample_age": f'time() - max(timestamp(DCGM_FI_DEV_GPU_UTIL{{{DCGM_NODE_LABEL}=~".+"}}))',
    "scrape_up": "count(up == 1)",
    "scrape_total": "count(up)",
    "alerts_firing": 'count(ALERTS{alertstate="firing"})',
}

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
    """Replace the global HTTP client; after None the next access rebuilds it."""
    global _client
    _client = client


async def close_client() -> None:
    """lifespan teardown: close the global client's connection pool."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


class PrometheusUnavailable(Exception):
    pass


def _extract_result(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate and extract data.result; malformed shapes become PrometheusUnavailable."""
    if data.get("status") != "success":
        raise PrometheusUnavailable(str(data))
    try:
        return list(data["data"]["result"])
    except (KeyError, TypeError) as exc:
        raise PrometheusUnavailable(f"malformed prometheus response: {exc}") from exc


def _extract_points(series: dict[str, Any]) -> list[tuple[float, float]]:
    """Extract a single series [(unix_ts, value)]; malformed shapes become PrometheusUnavailable."""
    try:
        return [(float(ts), float(v)) for ts, v in series["values"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise PrometheusUnavailable(f"malformed prometheus series: {exc}") from exc


async def _get(path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """GET the Prometheus HTTP API and extract data.result;
    network / status / payload-shape errors all become PrometheusUnavailable."""
    try:
        resp = await get_client().get(path, params=params)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PrometheusUnavailable(str(exc)) from exc
    return _extract_result(data)


async def query_range(
    metric: str, ns: str, pod: str, *, start: float, end: float, step: str
) -> list[tuple[float, float]]:
    """Return [(unix_ts, value)]; Prometheus unavailable raises PrometheusUnavailable."""
    results = await query_range_raw(
        QUERIES[metric].format(ns=ns, pod=pod), start=start, end=end, step=step
    )
    return _extract_points(results[0]) if results else []


async def query_range_raw(
    promql: str, *, start: float, end: float, step: str
) -> list[dict[str, Any]]:
    """Raw query_range result list (allow-listed templates, already formatted)."""
    return await _get(
        "/api/v1/query_range", {"query": promql, "start": start, "end": end, "step": step}
    )


async def query_range_multi(
    promql: str, *, start: float, end: float, step: str, group_label: str = DCGM_GPU_LABEL
) -> list[tuple[str, list[tuple[float, float]]]]:
    """group_label and data points per series, sorted by label string length then
    lexicographically."""
    results = await query_range_raw(promql, start=start, end=end, step=step)
    out: list[tuple[str, list[tuple[float, float]]]] = []
    for r in results:
        label = str(r.get("metric", {}).get(group_label, ""))
        out.append((label, [(float(ts), float(v)) for ts, v in r.get("values", [])]))
    out.sort(key=lambda x: (len(x[0]), x[0]))
    return out


async def query_instant(promql: str) -> float | None:
    """Scalar value of the first series of an instant query; None without data."""
    results = await _get("/api/v1/query", {"query": promql})
    if not results:
        return None
    try:
        return float(results[0]["value"][1])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise PrometheusUnavailable(f"malformed prometheus response: {exc}") from exc


async def query_instance_metric(
    metric: str, ns: str, pod: str, *, pool_label: str | None, start: float, end: float, step: str
) -> list[tuple[float, float]]:
    """Query instance metrics; in the hami pool GPU metrics prefer the HAMi templates and fall back
    to the defaults when empty."""
    if pool_label == POOL_HAMI and metric in HAMI_QUERIES:
        promql = HAMI_QUERIES[metric].format(ns=ns, pod=pod)
        results = await query_range_raw(promql, start=start, end=end, step=step)
        if results:
            return _extract_points(results[0])
    return await query_range(metric, ns, pod, start=start, end=end, step=step)
