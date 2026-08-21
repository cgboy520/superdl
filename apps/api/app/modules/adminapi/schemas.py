from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.platform_config import SettingGroup, SettingKind
from app.modules.billing.schemas import RechargeOut


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=64)


class AdminOut(BaseModel):
    id: int
    username: str
    role: str

    model_config = {"from_attributes": True}


class AdminToken(BaseModel):
    access_token: str
    token_type: str = "bearer"
    admin: AdminOut


# ---------- 管理端响应模型 ----------
# 金额一律 str(numeric 序列化,禁 float);时间为 isoformat 字符串。


class TenantOut(BaseModel):
    id: int
    phone_masked: str
    status: str
    balance: str
    total_consumed: str
    instances: int
    disk_gb: int
    created_at: str


class TenantStatusOut(BaseModel):
    id: int
    status: str


class NodeOut(BaseModel):
    name: str
    pool_label: str
    gpu_model: str  # canonical;未识别时为 "GPU"(展示兜底)
    gpu_total: int
    gpu_used: int
    status: str  # Ready / NotReady / Cordoned / Missing(台账口径,WP26)
    vcpu: int
    mem_gb: int
    disk_gb: int
    driver_version: str = ""
    cuda_version: str = ""
    # 台账扩展(WP26)
    gpu_model_raw: str = ""
    vram_gb: int = 0
    unlabeled: bool = False
    label_synced: bool = False
    last_seen: str = ""  # ISO;空=尚无台账行


class GpuModelAggregateOut(BaseModel):
    """台账按 canonical×池聚合(SKU「从集群资源创建」下拉数据源)。gpu_model=None 为未识别桶。"""

    gpu_model: str | None
    gpu_model_raw: str | None
    pool_label: str | None
    node_count: int
    gpu_total: int
    ready_gpu_total: int
    ready_gpu_free: int
    vram_gb: int


class ImageCoverageOut(BaseModel):
    """预热覆盖:cached/total 节点数与百分比(total=巡检登记的目标节点数)。"""

    cached: int
    total: int
    pct: int


class AdminImageOut(BaseModel):
    id: int
    framework: str
    framework_version: str
    python_version: str
    cuda_version: str
    image_ref: str
    prewarm_enabled: bool
    sort: int
    coverage: ImageCoverageOut
    failed_nodes: int


class ImageNodeCacheOut(BaseModel):
    node_name: str
    status: str  # pending / pulling / cached / failed
    last_error: str | None
    checked_at: datetime | None
    updated_at: datetime

    model_config = {"from_attributes": True}


class PrewarmEnqueuedOut(BaseModel):
    enqueued: int


class OversellPoolOut(BaseModel):
    pool: str
    physical_gpus: int
    sold_share: float
    oversell_ratio: float
    util_avg_24h: float | None


class ReconciliationOutlier(BaseModel):
    instance_id: int
    billed: str
    estimated: str
    diff_pct: float


class ReconciliationOut(BaseModel):
    day: str
    billed_total: str
    estimated_total: str
    diff_pct: float
    outliers: list[ReconciliationOutlier]


class AdminAlertOut(BaseModel):
    id: int
    type: str
    title: str
    content: str
    severity: str
    created_at: str


class AdjustmentOut(BaseModel):
    id: int
    user_id: int
    amount: str
    reason: str
    status: str
    created_by: int
    reviewed_by: int | None
    review_comment: str | None
    created_at: str


class AdjustmentStatusOut(BaseModel):
    id: int
    status: str


class AuditLogOut(BaseModel):
    id: int
    actor_type: str
    actor_id: str | None
    action: str
    target: str | None
    ip: str | None
    result: int
    created_at: str


class RevenueReportOut(BaseModel):
    today_revenue: str
    yesterday_revenue: str
    month_revenue: str
    today_signups: int
    yesterday_signups: int


class PolicySpecOut(BaseModel):
    kind: str
    min: str
    max: str


class PoliciesAdminOut(BaseModel):
    effective: dict[str, str]
    overrides: dict[str, str]
    specs: dict[str, PolicySpecOut]


class UpdatedKeysOut(BaseModel):
    updated: list[str]


class PlatformConfigItemOut(BaseModel):
    key: str
    group: SettingGroup
    kind: SettingKind
    choices: list[str]
    hint: str
    source: Literal["override", "env", "unset"]
    configured: bool
    value: str | None
    preview: str | None
    updated_at: str | None


class PlatformConfigOut(BaseModel):
    items: list[PlatformConfigItemOut]


class SmsTestOut(BaseModel):
    ok: bool
    provider: str


class AnnouncementResultOut(BaseModel):
    reached: int


class DeadTaskOut(BaseModel):
    id: int
    type: str
    payload: dict
    retries: int
    last_error: str | None
    created_at: str
    updated_at: str


class OutboxTaskStatusOut(BaseModel):
    id: int
    status: str


class PaymentAnomalyOut(BaseModel):
    kind: Literal["lost_callback", "closed_order", "negative_balance"]
    order_no: str | None
    user_id: int
    amount: str
    detail: str
    created_at: datetime


class OrderVerifyOut(BaseModel):
    order_no: str
    order_status: str
    order_amount: str
    channel_status: str
    channel_txn_id: str | None
    channel_amount: str | None
    matches: bool


class OrderBackfillOut(BaseModel):
    order_no: str
    status: str


class AdminOrderOut(RechargeOut):
    user_id: int


class CapacityWarningOut(BaseModel):
    """结构化警示(前端按 code 映射文案,params 供插值)。"""

    code: Literal["unrecognized_model", "no_ready_node", "vram_exceeds_node"]
    params: dict[str, Any] = {}


class CapacityPreviewOut(BaseModel):
    """SKU 表单容量预览(纯台账推算,不做库存预占)。"""

    matching_nodes: int
    ready_gpus: int
    total_gpus: int
    est_instances: int  # 共享档 = ready_gpus × ⌊100×oversell/pct⌋;其余 = ready_gpus
    warnings: list[CapacityWarningOut]
