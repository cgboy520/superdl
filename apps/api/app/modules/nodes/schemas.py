from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Pool = Literal["kata", "hami", "mig", "cpu"]
SwitchablePool = Literal["kata", "hami", "mig"]

HOSTNAME_PATTERN = r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$"


class EnrollmentCreate(BaseModel):
    pool: Pool
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
    status: str
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
    reason: str = Field(min_length=2, max_length=256)


class NodeDecommissionOut(BaseModel):
    """退役受理回执:停调度期望态 + 令牌作废已生效;删 Node 对象经 outbox 异步。"""

    node_name: str
    revoked_enrollments: int
    queued: bool = True


MAX_BOOTSTRAP_DICT_KEYS = 32


class BootstrapRequest(BaseModel):
    hostname: str = Field(min_length=1, max_length=253, pattern=HOSTNAME_PATTERN)
    os_info: dict[str, Any] = Field(default_factory=dict)
    gpu_details: list[dict[str, Any]] = Field(default_factory=list, max_length=16)

    @field_validator("os_info")
    @classmethod
    def _cap_os_info(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(v) > MAX_BOOTSTRAP_DICT_KEYS:
            raise ValueError(f"os_info 最多 {MAX_BOOTSTRAP_DICT_KEYS} 个键")
        return v

    @field_validator("gpu_details")
    @classmethod
    def _cap_gpu_details(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if any(len(item) > MAX_BOOTSTRAP_DICT_KEYS for item in v):
            raise ValueError(f"gpu_details 每项最多 {MAX_BOOTSTRAP_DICT_KEYS} 个键")
        return v


class BootstrapOut(BaseModel):
    """装机参数下发,含 join token 明文,不入日志。"""

    pool: str
    k8s_distro: str
    cluster_agent_version: str
    cluster_server_url: str
    cluster_join_token: str
    driver_version: str
    nvme_devices: list[str]
    registries_yaml: str
    registry_ca_pem: str = ""
    install_mirror: str = "official"
    progress_token: str
    script_sha256: str


class ProgressRequest(BaseModel):
    phase: str = Field(min_length=1, max_length=32)
    state: Literal["running", "ok", "failed", "rebooting"]
    message: str | None = Field(default=None, max_length=2000)
    driver_version: str | None = Field(default=None, max_length=32)
    cuda_version: str | None = Field(default=None, max_length=16)


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


ComponentStateOut = Literal["ok", "degraded", "down", "disabled", "unknown"]


class ComponentFactOut(BaseModel):
    """key 由前端映射文案,value 为不随语言变化的数据,tone 为显示色调。"""

    key: str
    value: str
    tone: Literal["normal", "warn", "bad"] = "normal"


class ComponentObjectOut(BaseModel):
    """抽屉对象表的一行(DaemonSet / listener / StorageClass / 节点);fields 的键由前端映射列名。"""

    name: str
    fields: dict[str, str]


class ClusterComponentOut(BaseModel):
    """组件事实与状态;文案由 key 映射,两个 hint 为不随语言变化的命令。"""

    key: ComponentKey
    state: ComponentStateOut
    headline: ComponentFactOut | None = None
    facts: list[ComponentFactOut] = Field(default_factory=list)
    objects: list[ComponentObjectOut] = Field(default_factory=list)
    fix_hint: str | None = None
    diag_hint: str | None = None


class ComponentProbeOut(BaseModel):
    """组件实时探测事实、Pod 与事件。"""

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
    registry_host: str | None
    registry_project: str | None


class ClusterStatusOut(BaseModel):
    """集群页数据(纯 DB 读能力缓存;「测试连接」同步探测后返回同形)。"""

    api_reachable: bool
    k8s_version: str | None
    distro: str | None
    probed_at: datetime | None
    pools: dict[str, int]
    pools_ready: dict[str, int]
    components: list[ClusterComponentOut]
    config: ClusterConfigStateOut
    error: str | None


class NodeOut(BaseModel):
    name: str
    pool_label: str
    gpu_model: str
    gpu_total: int
    gpu_used: int
    status: str
    vcpu: int
    mem_gb: int
    disk_gb: int
    driver_version: str = ""
    cuda_version: str = ""
    gpu_model_raw: str = ""
    vram_gb: int = 0
    unlabeled: bool = False
    label_synced: bool = False
    last_seen: str = ""
    desired_pool: str = ""
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
    vcpu_per_gpu: int
    mem_gb_per_gpu: int


class OversellPoolOut(BaseModel):
    pool: str
    sold_share: float
    oversell_ratio: float
    util_avg_24h: float | None
