import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

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

MAX_SSH_KEYS_PER_REQUEST = 50
MAX_ENV_VARS = 64
MAX_ENV_KEY_LEN = 128
MAX_ENV_VALUE_LEN = 4096


def strip_image_ref(value: object) -> object:
    """Strip the image reference, then hand it to the length and shape checks."""
    return value.strip() if isinstance(value, str) else value


def validate_market_shape(market: str, period: str | None, fields_set: set[str]) -> None:
    """Subscriptions must carry period; on-demand and spot requests must not pass period or
    period_count."""
    if market == MARKET_SUBSCRIPTION:
        if period is None:
            raise ValueError(render_message("orchestrator.periodRequired", None))
    elif "period" in fields_set or "period_count" in fields_set:
        raise ValueError(render_message("orchestrator.periodOnOnDemand", None))


def validate_health_path(health_path: str | None) -> None:
    if health_path is not None and not health_path.startswith("/"):
        raise ValueError(render_message("orchestrator.healthPathSlash", None))


def validate_user_env(env: dict[str, str] | None, secret_keys: list[str] | None) -> None:
    """Validate env count, key names, reserved names and value lengths; secret_keys must be a subset
    of the env keys."""
    env = env or {}
    if len(env) > MAX_ENV_VARS:
        raise ValueError(render_message("orchestrator.envTooMany", {"max": MAX_ENV_VARS}))
    for name, value in env.items():
        if len(name) > MAX_ENV_KEY_LEN or len(value) > MAX_ENV_VALUE_LEN:
            raise ValueError(
                render_message(
                    "orchestrator.envEntryTooLong",
                    {"name": name[:32], "key_max": MAX_ENV_KEY_LEN, "value_max": MAX_ENV_VALUE_LEN},
                )
            )
        if not _ENV_NAME_RE.match(name):
            raise ValueError(render_message("orchestrator.envKeyInvalid", {"name": name}))
        if name in _RESERVED_ENV_NAMES or name.startswith(_RESERVED_ENV_PREFIXES):
            raise ValueError(render_message("orchestrator.envKeyReserved", {"name": name}))
    for name in secret_keys or ():
        if name not in env:
            raise ValueError(render_message("orchestrator.envSecretKeyUnknown", {"name": name}))


class InstanceSubscriptionOut(BaseModel):
    """Subscription summary inlined in list / detail (full detail in billing.SubscriptionOut)."""

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
    """Renewal input; period may differ from the current one, re-quoted at the new period's
    discount."""

    period: Literal["day", "week", "month", "year"]
    period_count: int = Field(default=1, ge=1, le=MAX_PERIOD_COUNT)


class InstanceAutoRenew(BaseModel):
    enabled: bool


class RenewOut(BaseModel):
    """Renewal response: the latest instance state + this order's quote."""

    instance: "InstanceOut"
    quote: SubscriptionQuoteOut


class InstanceCreate(BaseModel):
    model_config = {"extra": "forbid"}

    sku_id: int
    gpu_count: int = Field(default=1, ge=0, le=8)
    image_ref: str = Field(min_length=1, max_length=256)
    ssh_key_ids: list[int] = Field(min_length=1, max_length=MAX_SSH_KEYS_PER_REQUEST)
    name: str | None = Field(default=None, max_length=64)
    data_disk_id: int | None = None

    market: Literal["on_demand", "subscription", "spot"] = MARKET_ON_DEMAND
    period: Literal["day", "week", "month", "year"] | None = None
    period_count: int = Field(default=1, ge=1, le=MAX_PERIOD_COUNT)

    _strip_image_ref = field_validator("image_ref", mode="before")(strip_image_ref)

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
    """Admin global instance view: with tenant and node (not exposed to the user console)."""

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
    """Access information: fields appear or are absent by form. dev = SSH + Jupyter; service
    revision
    instance = endpoint URL."""

    ssh_host: str | None = None
    ssh_port: int | None = None
    ssh_command: str | None = None
    jupyter_url: str | None = None
    endpoint_url: str | None = None


class InstanceLogsOut(BaseModel):
    """Container log split into lines; truncated=True means more than tail_lines, only the tail is
    returned."""

    lines: list[str]
    truncated: bool


class InstanceRename(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class AdminForceStopRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=256)


class PortPoolStatsOut(BaseModel):
    """SSH port pool level (admin); blocked = ports held by other cluster objects."""

    total: int
    assigned: int
    blocked: int
