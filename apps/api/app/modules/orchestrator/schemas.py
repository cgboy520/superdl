from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.money import MoneyOut


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


class InstanceRename(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class AdminForceStopRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=256)
