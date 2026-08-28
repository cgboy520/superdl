import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.core.messages import render_message
from app.core.money import MoneyOut
from app.core.pricing import MARKET_ON_DEMAND, MARKET_SUBSCRIPTION, MAX_PERIOD_COUNT
from app.modules.billing.schemas import SubscriptionQuoteOut
from app.modules.orchestrator import statemachine as sm_def

# 非终态清单(released 是唯一终态,历史行无界):状态机是唯一事实源,这里只做跨模块导出
# (模块边界只放行 service/schemas;管理端总览按它逐状态计数)
NON_TERMINAL_STATUSES: tuple[str, ...] = tuple(sm_def.TRANSITIONS)

# 实例形态。dev = SSH + JupyterLab 开发机;service = 对外 HTTPS 服务容器。
# 字面量在契约、模型 CHECK、build_pod_spec 分叉三处出现,这里是唯一事实源
WORKLOAD_DEV = "dev"
WORKLOAD_SERVICE = "service"

# 平台在实例容器内占用的端口:22 = sshd,8888 = JupyterLab。用户服务落在这两个上,
# 服务 Service 的 targetPort 会与 SSH / Jupyter 撞车。三层同源:契约层(本常量)、
# service 层(create_instance)、DB CHECK(models.ServiceEndpoint)
RESERVED_SERVICE_PORTS: tuple[int, ...] = (22, 8888)

# 平台自己往容器里注入的环境变量名段:JUPYTER_*(Jupyter 配置与 token)、SUPERDL_*(自留)、
# AUTHORIZED_KEYS(SSH 公钥注入)。允许用户覆盖它们,「用户环境变量」就成了改平台行为的口子
_RESERVED_ENV_PREFIXES = ("JUPYTER_", "SUPERDL_")
_RESERVED_ENV_NAMES = frozenset({"AUTHORIZED_KEYS"})
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# service 形态专属入参。dev 形态显式传任何一项都 422:build_pod_spec 的 dev 分支根本不读
# 它们,静默忽略会让用户以为「启动命令/环境变量已生效」,而实例跑的是镜像原样
_SERVICE_ONLY_FIELDS: tuple[str, ...] = (
    "container_command",
    "container_args",
    "env",
    "env_secret_keys",
    "service_port",
    "health_path",
    "require_api_key",
    "with_ssh",
)


class InstanceSubscriptionOut(BaseModel):
    """列表/详情内联的包周期概要(完整明细在 billing.SubscriptionOut)。"""

    period: str
    period_count: int
    # 下单时的 SKU **原价**时价快照。下发它是为了让续费预览与实扣同源:
    # 续费在后端就是按这个数重新报价的,前端拿不到它就只能用「折后价 ÷ 当前周期折扣」
    # 反推 —— 4 位单价的量化不可逆,反推值在长周期大卡数上会与实扣差到分级
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
    sku_id: int
    # 0 = CPU 实例(SKU 的 max_gpus_per_instance 也为 0);下界与上界的实际配对
    # 按 SKU 形态在 service.create_instance 判,契约层只挡明显越界
    gpu_count: int = Field(default=1, ge=0, le=8)
    image_ref: str = Field(min_length=1, max_length=256)
    # 不带 min_length:不开 SSH 的服务型实例本就没有公钥可选。「什么时候必须非空」
    # 由下面的 model_validator 按形态判(dev 恒需要,service 只在 with_ssh 时需要)
    ssh_key_ids: list[int] = Field(default_factory=list)
    name: str | None = Field(default=None, max_length=64)
    data_disk_id: int | None = None

    # ---- 服务型实例(dev 形态一项都不传)----
    workload_type: Literal["dev", "service"] = WORKLOAD_DEV
    container_command: list[str] | None = None
    container_args: list[str] | None = None
    # 用户环境变量。整包加密落库,其中 env_secret_keys 列出的键在 Pod 侧走 Secret,
    # 其余进 Pod spec 的明文 env
    env: dict[str, str] | None = None
    env_secret_keys: list[str] | None = None
    service_port: int | None = Field(default=None, ge=1, le=65535)
    health_path: str | None = Field(default=None, max_length=128)
    require_api_key: bool = True
    # 服务型实例默认不开 SSH:开了就要占一个 NodePort,而服务容器通常连 sshd 都没有
    with_ssh: bool = False

    # ---- 购买模式 ----
    # spot 与 subscription 互斥(market 是单值):竞价的对价是可被回收,
    # 而包周期的对价是买断一段时间,两者放一起没有任何自洽的语义
    market: Literal["on_demand", "subscription", "spot"] = MARKET_ON_DEMAND
    period: Literal["day", "week", "month", "year"] | None = None
    period_count: int = Field(default=1, ge=1, le=MAX_PERIOD_COUNT)

    @model_validator(mode="after")
    def _market_shape(self) -> "InstanceCreate":
        if self.market == MARKET_SUBSCRIPTION:
            if self.period is None:
                raise ValueError(render_message("orchestrator.periodRequired", None))
        elif "period" in self.model_fields_set or "period_count" in self.model_fields_set:
            # 按量单里带周期字段一律拒:静默忽略会让用户以为自己买的是包月,
            # 直到月底看见按小时出的账单才发现
            raise ValueError(render_message("orchestrator.periodOnOnDemand", None))
        return self

    @model_validator(mode="after")
    def _workload_shape(self) -> "InstanceCreate":
        if self.workload_type == WORKLOAD_DEV:
            # 判「显式传了」而不是「值非默认」:require_api_key/with_ssh 是布尔,
            # 按值判分不出「没传」与「传了刚好等于默认值」,后者会被静默放过
            extra = [f for f in _SERVICE_ONLY_FIELDS if f in self.model_fields_set]
            if extra:
                raise ValueError(
                    render_message(
                        "orchestrator.devWorkloadExtraFields", {"fields": "、".join(extra)}
                    )
                )
        elif self.service_port is None:
            raise ValueError(render_message("orchestrator.servicePortRequired", None))
        elif self.service_port in RESERVED_SERVICE_PORTS:
            raise ValueError(
                render_message("orchestrator.servicePortReserved", {"port": self.service_port})
            )
        if self.health_path is not None and not self.health_path.startswith("/"):
            raise ValueError(render_message("orchestrator.healthPathSlash", None))
        env = self.env or {}
        for name in env:
            if not _ENV_NAME_RE.match(name):
                raise ValueError(render_message("orchestrator.envKeyInvalid", {"name": name}))
            if name in _RESERVED_ENV_NAMES or name.startswith(_RESERVED_ENV_PREFIXES):
                raise ValueError(render_message("orchestrator.envKeyReserved", {"name": name}))
        for name in self.env_secret_keys or ():
            if name not in env:
                raise ValueError(render_message("orchestrator.envSecretKeyUnknown", {"name": name}))
        # 开了 SSH 却一把公钥都不选 = 建出一台谁也登不上去的实例(镜像不收口令登录)
        if not self.ssh_key_ids and (self.workload_type == WORKLOAD_DEV or self.with_ssh):
            raise ValueError(render_message("orchestrator.sshKeyRequired", None))
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
    # 服务型实例的端点 slug(dev 恒 None)。放在列表项里是为了让列表页零成本内联
    # 「[服务] svc-xxxx」——不然前端只能逐行去打 /service,而那正是 web.md 明令
    # 禁止的「接口调用随行数放大」。列表侧由一次批量查询回填(见 service._attach_slugs)
    service_slug: str | None = None
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

    dev = SSH + Jupyter;service = 端点 URL,开了 SSH 的服务实例两者都有。
    不按形态拆两个端点:前端拿到什么就渲染什么,少一次「先判形态再选接口」的分叉。
    """

    ssh_host: str | None = None
    ssh_port: int | None = None
    ssh_command: str | None = None
    jupyter_url: str | None = None
    endpoint_url: str | None = None


class ServiceEndpointOut(BaseModel):
    """对外服务端点视图(仅 workload_type='service' 的实例有)。"""

    slug: str
    url: str
    container_port: int
    protocol: str
    health_path: str | None
    require_api_key: bool
    # 就绪 = 实例 running 且巡检没观察到 Pod not-ready。服务实例持续 not-ready 不再判
    # failed(用户容器自己的 bug 不是平台故障),所以这一位是用户判断「我的服务起来没有」
    # 的唯一真相,不能拿 status 代替
    ready: bool
    # 容器配置回显(创建时写入,之后不可改)。env 只回明文项;密文项**只回键名**
    # (env_secret_keys)——回了值就等于给了一个把密文变量读回明文的端点,
    # 那正是「密文项落库即加密、不回显」这条承诺要防的事
    container_command: list[str] | None
    container_args: list[str] | None
    env: dict[str, str]
    env_secret_keys: list[str]
    created_at: datetime


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
