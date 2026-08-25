from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.money import MoneyOut
from app.modules.orchestrator import statemachine as sm_def

# 非终态清单(released 是唯一终态,历史行无界):状态机是唯一事实源,这里只做跨模块导出
# (模块边界只放行 service/schemas;管理端总览按它逐状态计数)
NON_TERMINAL_STATUSES: tuple[str, ...] = tuple(sm_def.TRANSITIONS)


class InstanceCreate(BaseModel):
    sku_id: int
    gpu_count: int = Field(default=1, ge=1, le=8)
    image_ref: str = Field(min_length=1, max_length=256)
    ssh_key_ids: list[int] = Field(min_length=1)
    name: str | None = Field(default=None, max_length=64)
    data_disk_id: int | None = None


class InstanceOut(BaseModel):
    id: int
    uuid: str
    name: str
    status: str
    sku_id: int
    spec: dict[str, Any]
    price_hourly: MoneyOut
    gpu_count: int
    image_ref: str
    ssh_port: int | None
    data_disk_id: int | None
    frozen_deadline: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminInstanceOut(InstanceOut):
    """管理端全局实例视图:含租户与调度节点(不暴露给用户端)。"""

    user_id: int
    node_name: str | None


class InstanceEventOut(BaseModel):
    id: int
    from_status: str | None
    to_status: str
    reason: str
    actor: str
    event_metadata: dict[str, Any] | None
    created_at: datetime

    model_config = {"from_attributes": True}


class InstanceAccessOut(BaseModel):
    ssh_host: str
    ssh_port: int
    ssh_command: str
    jupyter_url: str


class InstanceLogsOut(BaseModel):
    """容器日志:按行切分;truncated=True 表示日志量超过 tail_lines,只回了末尾段。"""

    lines: list[str]
    truncated: bool


class InstanceRename(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class AdminForceStopRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=256)


class PortPoolStatsOut(BaseModel):
    """SSH 端口池水位(管理端)。blocked=被集群其它对象撞占的端口,周期复检会放回。"""

    total: int
    assigned: int
    blocked: int
