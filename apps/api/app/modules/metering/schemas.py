from pydantic import BaseModel


class InstanceGpuSeries(BaseModel):
    uuid: str
    points: list[tuple[float, float]]  # (unix_ts, gpu_util%)
    last: float | None


class InstanceMetricsSummaryOut(BaseModel):
    """实例列表 sparkline 数据源。断源时 available=false(200,不 503)。"""

    available: bool
    items: list[InstanceGpuSeries]


class NodeGpuSeriesOut(BaseModel):
    """单卡多序列(DCGM):index 为卡序号,序列为 (unix_ts, 值) 对;断源的序列缺省。"""

    index: str
    util: list[tuple[float, float]] | None = None
    mem_used_mb: list[tuple[float, float]] | None = None
    temp: list[tuple[float, float]] | None = None


class NodeMetricsOut(BaseModel):
    """管理端节点每卡曲线 + 24h XID 计数。断源 available=false(200);grafana_url 可选。"""

    available: bool
    range: str
    gpus: list[NodeGpuSeriesOut]
    xid_count_24h: int
    grafana_url: str | None = None
