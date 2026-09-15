import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.core.messages import render_message
from app.core.money import MoneyOut
from app.core.pricing import MARKET_ON_DEMAND, MARKET_SUBSCRIPTION, MAX_PERIOD_COUNT
from app.modules.billing.schemas import SubscriptionQuoteOut
from app.modules.orchestrator import statemachine as sm_def

NON_TERMINAL_STATUSES: tuple[str, ...] = tuple(sm_def.TRANSITIONS)

WORKLOAD_DEV = "dev"
WORKLOAD_SERVICE = "service"

RESERVED_SERVICE_PORTS: tuple[int, ...] = (22, 8888)

_RESERVED_ENV_PREFIXES = ("JUPYTER_", "SUPERDL_", "NVIDIA_")
_RESERVED_ENV_NAMES = frozenset({"AUTHORIZED_KEYS"})
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_market_shape(market: str, period: str | None, fields_set: set[str]) -> None:
    """包周期必带 period;按量、竞价请求不得显式传 period 或 period_count。"""
    if market == MARKET_SUBSCRIPTION:
        if period is None:
            raise ValueError(render_message("orchestrator.periodRequired", None))
    elif "period" in fields_set or "period_count" in fields_set:
        raise ValueError(render_message("orchestrator.periodOnOnDemand", None))


def validate_health_path(health_path: str | None) -> None:
    if health_path is not None and not health_path.startswith("/"):
        raise ValueError(render_message("orchestrator.healthPathSlash", None))


def validate_user_env(env: dict[str, str] | None, secret_keys: list[str] | None) -> None:
    """校验环境变量键名与保留名;secret_keys 须为 env 键名的子集。"""
    env = env or {}
    for name in env:
        if not _ENV_NAME_RE.match(name):
            raise ValueError(render_message("orchestrator.envKeyInvalid", {"name": name}))
        if name in _RESERVED_ENV_NAMES or name.startswith(_RESERVED_ENV_PREFIXES):
            raise ValueError(render_message("orchestrator.envKeyReserved", {"name": name}))
    for name in secret_keys or ():
        if name not in env:
            raise ValueError(render_message("orchestrator.envSecretKeyUnknown", {"name": name}))


class InstanceSubscriptionOut(BaseModel):
    """列表/详情内联的包周期概要(完整明细在 billing.SubscriptionOut)。"""

    period: str
    period_count: int
    unit_price: MoneyOut
    started_at: datetime
    expires_at: datetime
    status: str
    auto_renew: bool
    amount_paid: MoneyOut

    model_config = {"from_attributes": True}


class InstanceRenew(BaseModel):
    """续费入参;period 可与当前周期不同,按新周期折扣重新报价。"""

    period: Literal["day", "week", "month", "year"]
    period_count: int = Field(default=1, ge=1, le=MAX_PERIOD_COUNT)


class InstanceAutoRenew(BaseModel):
    enabled: bool


class RenewOut(BaseModel):
    """续费响应:实例最新态 + 这一单的报价明细。"""

    instance: "InstanceOut"
    quote: SubscriptionQuoteOut


class InstanceCreate(BaseModel):
    model_config = {"extra": "forbid"}

    sku_id: int
    gpu_count: int = Field(default=1, ge=0, le=8)
    image_ref: str = Field(min_length=1, max_length=256)
    ssh_key_ids: list[int] = Field(min_length=1)
    name: str | None = Field(default=None, max_length=64)
    data_disk_id: int | None = None

    market: Literal["on_demand", "subscription", "spot"] = MARKET_ON_DEMAND
    period: Literal["day", "week", "month", "year"] | None = None
    period_count: int = Field(default=1, ge=1, le=MAX_PERIOD_COUNT)

    @model_validator(mode="after")
    def _market_shape(self) -> "InstanceCreate":
        validate_market_shape(self.market, self.period, self.model_fields_set)
        return self


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
    workload_type: str
    market: str
    subscription: InstanceSubscriptionOut | None = None
    with_ssh: bool
    ssh_port: int | None
    service_slug: str | None = None
    service_revision: int | None = None
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
    """接入信息:字段随形态出现或缺席。dev = SSH + Jupyter;服务版本实例 = 端点 URL。"""

    ssh_host: str | None = None
    ssh_port: int | None = None
    ssh_command: str | None = None
    jupyter_url: str | None = None
    endpoint_url: str | None = None


class InstanceLogsOut(BaseModel):
    """容器日志:按行切分;truncated=True 表示超过 tail_lines,只回末尾段。"""

    lines: list[str]
    truncated: bool


class InstanceRename(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class AdminForceStopRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=256)


class PortPoolStatsOut(BaseModel):
    """SSH 端口池水位(管理端);blocked = 被集群其它对象占用的端口。"""

    total: int
    assigned: int
    blocked: int
