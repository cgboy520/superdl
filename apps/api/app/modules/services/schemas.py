from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.core.messages import render_message
from app.core.pricing import MARKET_ON_DEMAND, MAX_PERIOD_COUNT
from app.modules.orchestrator.schemas import (
    RESERVED_SERVICE_PORTS,
    InstanceEventOut,
    InstanceOut,
    validate_health_path,
    validate_market_shape,
    validate_user_env,
)


class ServiceSpecIn(BaseModel):
    """一个版本的完整规格:部署与版本更新共用。每个字段都快照到那一版的实例上,之后不可改。"""

    sku_id: int
    # 0 = CPU 实例(SKU 的 max_gpus_per_instance 也为 0);配对在 orchestrator 判
    gpu_count: int = Field(default=1, ge=0, le=8)
    image_ref: str = Field(min_length=1, max_length=256)
    # 不开 SSH 的服务没有公钥可选;with_ssh 时必须非空(下面的 validator)
    ssh_key_ids: list[int] = Field(default_factory=list)
    data_disk_id: int | None = None
    # 服务默认不开 SSH:开了就要占一个 NodePort,而服务容器通常连 sshd 都没有
    with_ssh: bool = False
    container_command: list[str] | None = None
    container_args: list[str] | None = None
    # 用户环境变量。整包加密落库,其中 env_secret_keys 列出的键在 Pod 侧走 Secret,
    # 其余进 Pod spec 的明文 env
    env: dict[str, str] | None = None
    env_secret_keys: list[str] | None = None
    service_port: int = Field(ge=1, le=65535)
    health_path: str | None = Field(default=None, max_length=128)
    # spot 与 subscription 互斥(market 是单值):可被回收与买断一段时间没有自洽的合并语义
    market: Literal["on_demand", "subscription", "spot"] = MARKET_ON_DEMAND
    period: Literal["day", "week", "month", "year"] | None = None
    period_count: int = Field(default=1, ge=1, le=MAX_PERIOD_COUNT)

    @model_validator(mode="after")
    def _shape(self) -> "ServiceSpecIn":
        validate_market_shape(self.market, self.period, self.model_fields_set)
        if self.service_port in RESERVED_SERVICE_PORTS:
            raise ValueError(
                render_message("orchestrator.servicePortReserved", {"port": self.service_port})
            )
        validate_health_path(self.health_path)
        validate_user_env(self.env, self.env_secret_keys)
        # 开了 SSH 却一把公钥都不选 = 建出一台谁也登不上去的实例(镜像不收口令登录)
        if self.with_ssh and not self.ssh_key_ids:
            raise ValueError(render_message("orchestrator.sshKeyRequired", None))
        return self


class ServiceCreate(ServiceSpecIn):
    name: str | None = Field(default=None, max_length=64)
    require_api_key: bool = True
    protocol: Literal["http"] = "http"


class ServicePatch(BaseModel):
    """改名与鉴权开关:两者都只改 services 行,不重新部署。"""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    require_api_key: bool | None = None


class ServiceContainerOut(BaseModel):
    """当前版本的容器配置回显(创建那一版时写入,之后不可改;要改请更新版本)。
    env 只回明文项,密文项只回键名:回值就等于给了一个把密文变量读回明文的端点。"""

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
    # 派生状态(state.py),不落库;ready 是「服务起来没有」的唯一真相,不能拿 status 代替
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
    """管理端全局服务视图:含租户与当前版本实例的调度节点(不暴露给用户端)。"""

    user_id: int
    node_name: str | None = None


class ServiceEventOut(InstanceEventOut):
    """服务级时间线 = 全部版本实例的事件并集,标出事件属于哪一版。"""

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
    """创建响应:明文 key 只在这一次出现,库里只有 HMAC 摘要,关掉就找不回来。"""

    key: str
