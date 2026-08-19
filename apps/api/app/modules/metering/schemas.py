from pydantic import BaseModel


class InstanceGpuSeries(BaseModel):
    uuid: str
    points: list[tuple[float, float]]  # (unix_ts, gpu_util%)
    last: float | None


class InstanceMetricsSummaryOut(BaseModel):
    """实例列表 sparkline 数据源。断源时 available=false(200,不 503)。"""

    available: bool
    items: list[InstanceGpuSeries]
