from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.messages import render_message
from app.core.pricing import MARKET_ON_DEMAND, MAX_PERIOD_COUNT
from app.modules.orchestrator.schemas import (
    MAX_SSH_KEYS_PER_REQUEST,
    RESERVED_SERVICE_PORTS,
    InstanceEventOut,
    InstanceOut,
    strip_image_ref,
    validate_health_path,
    validate_market_shape,
    validate_user_env,
)

MAX_CONTAINER_ARGV = 64
MAX_CONTAINER_ARG_LEN = 4096
ArgvItem = Annotated[str, Field(max_length=MAX_CONTAINER_ARG_LEN)]


class ServiceSpecIn(BaseModel):
    """一个版本的完整规格(部署与版本更新共用),快照到那一版的实例上,之后不可改。"""

    sku_id: int
    gpu_count: int = Field(default=1, ge=0, le=8)
    image_ref: str = Field(min_length=1, max_length=256)
    ssh_key_ids: list[int] = Field(default_factory=list, max_length=MAX_SSH_KEYS_PER_REQUEST)
    data_disk_id: int | None = None
    with_ssh: bool = False
    container_command: list[ArgvItem] | None = Field(default=None, max_length=MAX_CONTAINER_ARGV)
    container_args: list[ArgvItem] | None = Field(default=None, max_length=MAX_CONTAINER_ARGV)
    env: dict[str, str] | None = None
    env_secret_keys: list[str] | None = Field(default=None, max_length=64)
    service_port: int = Field(ge=1, le=65535)
    health_path: str | None = Field(default=None, max_length=128)
    market: Literal["on_demand", "subscription", "spot"] = MARKET_ON_DEMAND
    period: Literal["day", "week", "month", "year"] | None = None
    period_count: int = Field(default=1, ge=1, le=MAX_PERIOD_COUNT)

    _strip_image_ref = field_validator("image_ref", mode="before")(strip_image_ref)

    @model_validator(mode="after")
    def _shape(self) -> "ServiceSpecIn":
        validate_market_shape(self.market, self.period, self.model_fields_set)
        if self.service_port in RESERVED_SERVICE_PORTS:
            raise ValueError(
                render_message("orchestrator.servicePortReserved", {"port": self.service_port})
            )
        validate_health_path(self.health_path)
        validate_user_env(self.env, self.env_secret_keys)
        if self.with_ssh and not self.ssh_key_ids:
            raise ValueError(render_message("orchestrator.sshKeyRequired", None))
        return self


class ServiceCreate(ServiceSpecIn):
    name: str | None = Field(default=None, max_length=64)
    require_api_key: bool = True
    protocol: Literal["http"] = "http"


class ServiceRevisionCreate(ServiceSpecIn):
    """版本更新的完整规格(与部署同一形态)。env_secret_keep = 沿用当前版本密文值的键名;
    同名键同时出现在 env 里以 env 为准。"""

    env_secret_keep: list[str] = Field(default_factory=list, max_length=64)


class ServicePatch(BaseModel):
    """改名与鉴权开关:只改 services 行。"""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    require_api_key: bool | None = None


class ServiceContainerOut(BaseModel):
    """当前版本的容器配置回显(不可改,要改请更新版本);env 只回明文项,密文项只回键名。"""

    image_ref: str
    container_command: list[str] | None
    container_args: list[str] | None
    env: dict[str, str]
    env_secret_keys: list[str]
    service_port: int | None
    health_path: str | None
    with_ssh: bool


class ServiceOut(BaseModel):
    id: int
    slug: str
    name: str
    url: str
    protocol: str
    require_api_key: bool
    desired_state: str
    status: str
    ready: bool
    revision: int
    current_instance: InstanceOut | None
    rollout_instance: InstanceOut | None
    container: ServiceContainerOut | None
    created_at: datetime
    updated_at: datetime
    released_at: datetime | None


class AdminServiceOut(ServiceOut):
    """管理端全局服务视图:含租户与当前版本实例的调度节点。"""

    user_id: int
    node_name: str | None = None


class ServiceEventOut(InstanceEventOut):
    """服务级时间线:全部版本实例的事件并集,标出所属版本。"""

    instance_uuid: str
    revision: int | None


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class ApiKeyOut(BaseModel):
    id: int
    name: str
    key_prefix: str
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ApiKeyCreateOut(ApiKeyOut):
    """创建响应:明文 key 只在这一次出现。"""

    key: str
