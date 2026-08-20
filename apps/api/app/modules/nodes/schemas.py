from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Pool = Literal["kata", "hami", "mig"]

HOSTNAME_PATTERN = r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$"


class EnrollmentCreate(BaseModel):
    pool: Pool
    hostname: str | None = Field(default=None, max_length=253, pattern=HOSTNAME_PATTERN)
    note: str | None = Field(default=None, max_length=128)
    nvme_devices: list[str] | None = Field(default=None, max_length=16)
    ttl_hours: int = Field(default=24, ge=1, le=168)


class NodeEnrollmentOut(BaseModel):
    """列表/进度视图 —— 永不含 token。"""

    id: int
    pool: str
    hostname: str | None
    note: str | None
    status: str  # pending/installing/rebooting/joining/joined/failed/expired/revoked
    phase: str | None
    error: str | None
    node_name: str | None
    reported_ip: str | None
    expires_at: datetime
    last_report_at: datetime | None
    joined_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class EnrollmentCommandOut(BaseModel):
    """创建/重生成响应:token 明文仅此一次出现。"""

    enrollment: NodeEnrollmentOut
    token: str
    curl_command: str
    wget_command: str


# ---------- 匿名侧(节点上的脚本调用) ----------


class BootstrapRequest(BaseModel):
    hostname: str = Field(min_length=1, max_length=253, pattern=HOSTNAME_PATTERN)
    os_info: dict[str, Any] = Field(default_factory=dict)  # {os_release, kernel, arch}
    gpus: list[str] = Field(default_factory=list, max_length=16)  # lspci/nvidia-smi 摘要


class BootstrapOut(BaseModel):
    """装机参数下发 —— 含 join token 明文,仅经 Bearer POST 响应体,严禁入日志。"""

    pool: str
    hostname_expected: str | None
    rke2_version: str
    rke2_server_url: str
    rke2_join_token: str
    driver_version: str
    nvme_devices: list[str]
    registries_yaml: str  # WP22 镜像缓存 mirror 配置正文(可空串)


class ProgressRequest(BaseModel):
    phase: str = Field(min_length=1, max_length=32)
    state: Literal["running", "ok", "failed", "rebooting"]
    message: str | None = Field(default=None, max_length=2000)


class ProgressAck(BaseModel):
    status: str  # 服务端视角的 enrollment 状态(脚本可据此提前退出)
