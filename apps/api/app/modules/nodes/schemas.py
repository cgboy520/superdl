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
    """List / progress view, without the token."""

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
    """Create / regenerate response: the token plaintext appears here once only."""

    enrollment: NodeEnrollmentOut
    token: str
    curl_command: str
    wget_command: str


class NodeDecommissionRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=256)


class NodeDecommissionOut(BaseModel):
    """Decommission acceptance receipt: cordon desired state + token revocation are effective;
    deleting the Node object is asynchronous via outbox."""

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
            raise ValueError(f"os_info may have at most {MAX_BOOTSTRAP_DICT_KEYS} keys")
        return v

    @field_validator("gpu_details")
    @classmethod
    def _cap_gpu_details(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if any(len(item) > MAX_BOOTSTRAP_DICT_KEYS for item in v):
            raise ValueError(
                f"each gpu_details item may have at most {MAX_BOOTSTRAP_DICT_KEYS} keys"
            )
        return v


class BootstrapOut(BaseModel):
    """Install parameters handed to the node, join token plaintext included, never logged."""

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
    """key is mapped to copy by the frontend, value is data that does not vary by language, tone is
    the display tone."""

    key: str
    value: str
    tone: Literal["normal", "warn", "bad"] = "normal"


class ComponentObjectOut(BaseModel):
    """One row of the drawer object table (DaemonSet / listener / StorageClass / node); the keys of
    fields are mapped to column names by the frontend."""

    name: str
    fields: dict[str, str]


class ClusterComponentOut(BaseModel):
    """Component facts and state; copy is mapped by key, the two hints are commands that do not vary
    by language."""

    key: ComponentKey
    state: ComponentStateOut
    headline: ComponentFactOut | None = None
    facts: list[ComponentFactOut] = Field(default_factory=list)
    objects: list[ComponentObjectOut] = Field(default_factory=list)
    fix_hint: str | None = None
    diag_hint: str | None = None


class ComponentProbeOut(BaseModel):
    """Live component probe facts, Pods and events."""

    key: ComponentKey
    probed_at: datetime
    facts: list[ComponentFactOut] = Field(default_factory=list)
    pods: list[ComponentObjectOut] = Field(default_factory=list)
    events: list[ComponentObjectOut] = Field(default_factory=list)


class ClusterConfigStateOut(BaseModel):
    """Configuration readiness flags (no plaintext)."""

    server_url_set: bool
    join_token_set: bool
    prometheus_url_set: bool
    grafana_url: str | None
    registry_host: str | None
    registry_project: str | None


class ClusterStatusOut(BaseModel):
    """Cluster page data (pure DB read of the capability cache; "test connection" probes
    synchronously and returns the same shape)."""

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
    #: Pool-switch capabilities of the canonical GPU model (`core/gpu_models`, fail-closed for
    #: unrecognised models); the consoles read these instead of keeping their own tables.
    supports_mig: bool = False
    supports_passthrough: bool = False


class GpuModelAggregateOut(BaseModel):
    """Inventory aggregated by canonical × pool. gpu_model=None is the unrecognised bucket."""

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
