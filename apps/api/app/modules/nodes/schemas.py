from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# cpu = 无卡节点池;装机不打 NVIDIA operand 标签、跳过 GPU 探测
Pool = Literal["kata", "hami", "mig", "cpu"]
# 可在线互切的池(core/gpu_adapter.SWITCHABLE_POOLS 的 Literal 版);cpu 是无卡机的物理属性
SwitchablePool = Literal["kata", "hami", "mig"]

HOSTNAME_PATTERN = r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$"


class EnrollmentCreate(BaseModel):
    pool: Pool
    # 签发时绑定期望主机名;上报不符即 failed
    hostname: str = Field(min_length=1, max_length=253, pattern=HOSTNAME_PATTERN)
    note: str | None = Field(default=None, max_length=128)
    nvme_devices: list[str] | None = Field(default=None, max_length=16)
    ttl_hours: int = Field(default=24, ge=1, le=168)


class NodeEnrollmentOut(BaseModel):
    """列表/进度视图,不含 token。"""

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


class NodeDecommissionRequest(BaseModel):
    # 原因必填,落审计 detail
    reason: str = Field(min_length=2, max_length=256)


class NodeDecommissionOut(BaseModel):
    """退役受理回执:停调度期望态 + 令牌作废已生效;删 Node 对象经 outbox 异步。"""

    node_name: str
    revoked_enrollments: int  # 本次被置 revoked 的注册登记行数
    queued: bool = True


# ---------- 匿名侧(节点上的脚本调用) ----------


class BootstrapRequest(BaseModel):
    hostname: str = Field(min_length=1, max_length=253, pattern=HOSTNAME_PATTERN)
    os_info: dict[str, Any] = Field(default_factory=dict)  # {os_release, kernel, arch}
    # 全卡清单 [{name, memory_mib?}];无驱动时 lspci 名称回落(无 memory_mib)
    gpu_details: list[dict[str, Any]] = Field(default_factory=list, max_length=16)


class BootstrapOut(BaseModel):
    """装机参数下发,含 join token 明文,不入日志。"""

    pool: str
    k8s_distro: str  # rke2 | k3s,平台探测派生
    cluster_agent_version: str
    cluster_server_url: str
    cluster_join_token: str
    driver_version: str
    nvme_devices: list[str]
    registries_yaml: str  # 节点 registries.yaml 正文(Spegel / Harbor 代理缓存 / CA;不含凭据)
    registry_ca_pem: str = ""  # Harbor 自签/私有 CA(PEM);非空时脚本落 $RANCHER_DIR/harbor-ca.crt
    install_mirror: str = "cn"  # 装机安装源:cn=rancher 国内镜像 / official
    # bootstrap 换发的窄权限令牌(仅 /progress),此后上报只用它
    progress_token: str
    script_sha256: str  # 当前下发脚本的指纹:重启续跑重拉自身时校验,防中途替换


class ProgressRequest(BaseModel):
    phase: str = Field(min_length=1, max_length=32)
    state: Literal["running", "ok", "failed", "rebooting"]
    message: str | None = Field(default=None, max_length=2000)
    # 驱动/CUDA 版本:收尾上报(waiting_node)附带,写进登记快照 os_info
    driver_version: str | None = Field(default=None, max_length=32)
    cuda_version: str | None = Field(default=None, max_length=16)


# ---------- 集群页(管理端) ----------


ComponentKey = Literal[
    "nodes",
    "hami",
    "gpu_operator",
    "dcgm",
    "nvidia_runtimeclass",
    "kata_runtimeclass",
    "storage",
    "gateway",
    "cert_manager",
    "monitoring",
]


# 体检五态:ok 全就绪 / degraded 部分就绪 / down 缺位或全挂 / disabled 能力未开 / unknown 快照过期
ComponentStateOut = Literal["ok", "degraded", "down", "disabled", "unknown"]


class ComponentFactOut(BaseModel):
    """一条可核对的事实。key 由前端映射 label;value 是纯数据(计数 / 版本 / 对象名 / 地址),
    不随语言。tone 供前端着色,文案里不写形容词。"""

    key: str
    value: str
    tone: Literal["normal", "warn", "bad"] = "normal"


class ComponentObjectOut(BaseModel):
    """抽屉对象表的一行(DaemonSet / listener / StorageClass / 节点);fields 的键由前端映射列名。"""

    name: str
    fields: dict[str, str]


class ClusterComponentOut(BaseModel):
    """组件体检项。文案全部由 key 映射(判据 / 影响面 / 事实 label 在两端 locales),
    后端只出事实数据;两个 hint 是命令,不随语言。"""

    key: ComponentKey
    state: ComponentStateOut
    headline: ComponentFactOut | None = None
    facts: list[ComponentFactOut] = Field(default_factory=list)
    objects: list[ComponentObjectOut] = Field(default_factory=list)
    # 修复命令:仅 down / degraded 时给
    fix_hint: str | None = None
    # 排障第一步,一直给(取自 deploy/cluster/runbooks/cluster-validation.md)
    diag_hint: str | None = None


class ComponentProbeOut(BaseModel):
    """体检项的实时深探结果。快照答「就绪几个」,深探答「为什么不就绪」。"""

    key: ComponentKey
    probed_at: datetime
    facts: list[ComponentFactOut] = Field(default_factory=list)
    pods: list[ComponentObjectOut] = Field(default_factory=list)
    events: list[ComponentObjectOut] = Field(default_factory=list)


class ClusterConfigStateOut(BaseModel):
    """配置就绪位(不回明文)。"""

    server_url_set: bool
    join_token_set: bool
    prometheus_url_set: bool
    grafana_url: str | None
    # 镜像仓库地址与平台项目(非密),ops/readonly 可读
    registry_host: str | None
    registry_project: str | None


class ClusterStatusOut(BaseModel):
    """集群页数据(纯 DB 读能力缓存;「测试连接」同步探测后返回同形)。"""

    api_reachable: bool
    k8s_version: str | None
    distro: str | None
    probed_at: datetime | None
    pools: dict[str, int]
    # 池→Ready 且可调度的节点数:档位能不能卖看这个,组件体检不再掺业务解读
    pools_ready: dict[str, int]
    components: list[ClusterComponentOut]
    config: ClusterConfigStateOut
    error: str | None


# ---------- 节点台账(管理端) ----------


class NodeOut(BaseModel):
    name: str
    pool_label: str
    gpu_model: str  # canonical;未识别时为 "GPU"
    gpu_total: int
    gpu_used: int
    status: str  # Ready / NotReady / Cordoned / Missing(台账口径)
    vcpu: int
    mem_gb: int
    disk_gb: int
    driver_version: str = ""
    cuda_version: str = ""
    # 台账扩展
    gpu_model_raw: str = ""
    vram_gb: int = 0
    unlabeled: bool = False
    label_synced: bool = False
    last_seen: str = ""  # ISO;空=尚无台账行
    # 期望池:非空 = 管理端已发起切池,标签收敛前与 pool_label 不一致
    desired_pool: str = ""
    # 节点上未释放实例数(含已关机/冻结/失败);切池与退役的前置判据
    active_instances: int = 0


class GpuModelAggregateOut(BaseModel):
    """台账按 canonical×池聚合。gpu_model=None 为未识别桶。"""

    gpu_model: str | None
    gpu_model_raw: str | None
    pool_label: str | None
    node_count: int
    gpu_total: int
    ready_gpu_total: int
    ready_gpu_free: int
    vram_gb: int
    vcpu_per_gpu: int  # Ready 节点整机配比最小值(vCPU÷卡数),0=未知
    mem_gb_per_gpu: int


class OversellPoolOut(BaseModel):
    pool: str
    sold_share: float
    oversell_ratio: float
    util_avg_24h: float | None
