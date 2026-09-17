from pydantic import BaseModel


class InstanceGpuSeries(BaseModel):
    uuid: str
    points: list[tuple[float, float]]
    last: float | None


class InstanceMetricsSummaryOut(BaseModel):
    """Data source of the instance list sparklines. Source down → available=false (200, not 503)."""

    available: bool
    items: list[InstanceGpuSeries]


class NodeGpuSeriesOut(BaseModel):
    """Per-card multi-series (DCGM): index is the card index, series are (unix_ts, value) pairs;
    series from a down source are absent."""

    index: str
    util: list[tuple[float, float]] | None = None
    mem_used_mb: list[tuple[float, float]] | None = None
    temp: list[tuple[float, float]] | None = None


class NodeMetricsOut(BaseModel):
    """Admin per-card node curves + 24 h XID count. Source down → available=false (200); grafana_url
    optional."""

    available: bool
    range: str
    gpus: list[NodeGpuSeriesOut]
    xid_count_24h: int
    grafana_url: str | None = None


class InstanceMetricsOut(BaseModel):
    """Instance monitoring curves: metric name → (unix_ts, value) series; metric set in
    prom.QUERIES."""

    range: str
    series: dict[str, list[tuple[float, float]]]


class ReconciliationOutlier(BaseModel):
    instance_id: int
    billed: str
    estimated: str
    diff_pct: float


class ReconciliationOut(BaseModel):
    day: str
    billed_total: str
    estimated_total: str
    diff_pct: float
    outliers: list[ReconciliationOutlier]
