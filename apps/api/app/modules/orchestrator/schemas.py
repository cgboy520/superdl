import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.core.messages import render_message
from app.core.money import MoneyOut
from app.core.pricing import MARKET_ON_DEMAND, MARKET_SUBSCRIPTION, MAX_PERIOD_COUNT
from app.modules.billing.schemas import SubscriptionQuoteOut
from app.modules.orchestrator import statemachine as sm_def

# 非终态清单(released 是唯一终态):状态机是唯一事实源,这里只为跨模块导出
# (模块边界只放行 service/schemas)
NON_TERMINAL_STATUSES: tuple[str, ...] = tuple(sm_def.TRANSITIONS)

# 实例形态。dev = SSH + JupyterLab 开发机;service = 对外 HTTPS 服务容器。
# 字面量在契约、模型 CHECK、build_pod_spec 分叉三处出现,这里是唯一事实源
WORKLOAD_DEV = "dev"
WORKLOAD_SERVICE = "service"

# 平台在实例容器内占用的端口:22 = sshd,8888 = JupyterLab,用户服务落上去会与其
# targetPort 撞车。三层同源:契约层(本常量)、service 层、DB CHECK(models.ServiceEndpoint)
RESERVED_SERVICE_PORTS: tuple[int, ...] = (22, 8888)

# 平台自己往容器里注入的环境变量名段:JUPYTER_*(Jupyter 配置与 token)、SUPERDL_*(自留)、
# AUTHORIZED_KEYS(SSH 公钥注入)。允许用户覆盖它们,「用户环境变量」就成了改平台行为的口子。
# NVIDIA_* 同列:kubelet 合并 env 时 pod spec 里的重复键后写胜出,用户声明
# NVIDIA_VISIBLE_DEVICES=all 可覆盖 device-plugin 的分配结果,看到节点上全部物理卡
# (准入层 superdl-tenant-pod-baseline 另有同口径 CEL 规则,双层兜底)
_RESERVED_ENV_PREFIXES = ("JUPYTER_", "SUPERDL_", "NVIDIA_")
_RESERVED_ENV_NAMES = frozenset({"AUTHORIZED_KEYS"})
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_market_shape(market: str, period: str | None, fields_set: set[str]) -> None:
    """购买模式与周期字段的配对(创建实例与部署服务共用):包周期必带周期;
    按量 / 竞价单里带周期字段一律拒,静默忽略会让用户以为自己买的是包月。"""
    if market == MARKET_SUBSCRIPTION:
        if period is None:
            raise ValueError(render_message("orchestrator.periodRequired", None))
    elif "period" in fields_set or "period_count" in fields_set:
        raise ValueError(render_message("orchestrator.periodOnOnDemand", None))


def validate_health_path(health_path: str | None) -> None:
    if health_path is not None and not health_path.startswith("/"):
        raise ValueError(render_message("orchestrator.healthPathSlash", None))


def validate_user_env(env: dict[str, str] | None, secret_keys: list[str] | None) -> None:
    """用户环境变量键名(与 build_pod_spec 注入项同源的黑名单)与密文键子集关系。"""
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
    # 下单时的 SKU 原价时价快照。下发它让续费预览与实扣同源:4 位单价的量化不可逆,
    # 前端拿折后价反推会在长周期大卡数上与实扣差到分级
    unit_price: MoneyOut
    started_at: datetime
    expires_at: datetime
    status: str
    auto_renew: bool
    amount_paid: MoneyOut

    model_config = {"from_attributes": True}


class InstanceRenew(BaseModel):
    """续费入参。period 可与当前周期不同(包月转包年),按新周期的折扣重新报价。"""

    period: Literal["day", "week", "month", "year"]
    period_count: int = Field(default=1, ge=1, le=MAX_PERIOD_COUNT)


class InstanceAutoRenew(BaseModel):
    enabled: bool


class RenewOut(BaseModel):
    """续费响应:实例最新态 + 这一单的报价明细(前端直接渲染成收据)。"""

    instance: "InstanceOut"
    quote: SubscriptionQuoteOut


class InstanceCreate(BaseModel):
    # 拒收未知字段:服务容器参数(启动命令 / 端口 / env)属于 /services,打到这里静默忽略
    # 会让用户以为「启动命令已生效」而实例跑的是镜像原样
    model_config = {"extra": "forbid"}

    sku_id: int
    # 0 = CPU 实例(SKU 的 max_gpus_per_instance 也为 0);实际配对按 SKU 形态在
    # service.create_instance 判,契约层只挡明显越界
    gpu_count: int = Field(default=1, ge=0, le=8)
    image_ref: str = Field(min_length=1, max_length=256)
    # 开发机只有密钥登录:一把公钥都不选 = 建出一台谁也登不上去的实例
    ssh_key_ids: list[int] = Field(min_length=1)
    name: str | None = Field(default=None, max_length=64)
    data_disk_id: int | None = None

    # ---- 购买模式 ----
    # spot 与 subscription 互斥(market 是单值):可被回收与买断一段时间没有自洽的合并语义
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
    # 购买模式(on_demand / subscription / spot)。包周期实例的到期信息在 subscription 里,
    # 由列表侧一次批量查询回填(见 service._attach_subscriptions),不逐行打接口
    market: str
    subscription: InstanceSubscriptionOut | None = None
    # 前端「连接」栏按它决定显不显示 SSH 那一块。dev 恒 True;service 由用户勾选。
    # 不拿 ssh_port 是否为空代替:端口是 outbox 建 Pod 时才分配的,creating 期间恒空
    with_ssh: bool
    ssh_port: int | None
    # 所属在线服务的 slug 快照(dev 恒 None);服务实例默认不进用户端实例列表
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
    """接入信息:字段随形态出现或缺席,不是「恒有值」的契约。

    dev = SSH + Jupyter;服务的版本实例 = 端点 URL,开了 SSH 的两者都有。
    不按形态拆两个端点:前端拿到什么就渲染什么,少一次「先判形态再选接口」的分叉。
    """

    ssh_host: str | None = None
    ssh_port: int | None = None
    ssh_command: str | None = None
    jupyter_url: str | None = None
    endpoint_url: str | None = None


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
