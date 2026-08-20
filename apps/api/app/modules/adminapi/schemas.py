from datetime import datetime
from typing import Literal

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
# 此前这批端点返回裸 dict,OpenAPI 无精确 schema,admin 前端被迫手写行类型
# + as unknown as 断言(契约名存实亡)。金额一律 str(numeric 序列化),
# 已 isoformat 的时间保持 str 以维持线上 JSON 形状不变。


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
    gpu_model: str
    gpu_total: int
    gpu_used: int
    status: str
    vcpu: int
    mem_gb: int
    disk_gb: int
    driver_version: str = ""
    cuda_version: str = ""


class ImageCoverageOut(BaseModel):
    """预热覆盖(WP22):cached/total 节点数与百分比(total=巡检登记的目标节点数)。"""

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
