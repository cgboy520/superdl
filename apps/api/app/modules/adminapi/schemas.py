from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.platform_config import PlatformConfigGroup, PlatformConfigKind
from app.modules.billing.schemas import LedgerEntryOut, RechargeOut


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    # 与创建/重置同标准(128)
    password: str = Field(min_length=1, max_length=128)


class AdminOut(BaseModel):
    id: int
    username: str
    role: str

    model_config = {"from_attributes": True}


AdminRole = Literal["admin", "ops", "finance", "readonly"]

# 只含 reason 的请求体共用的原因长度上限。不合并成一个 ReasonBody:orval 按 schema 名生成前端类型
REASON_MAX_LENGTH = 256


class AdminAccountOut(BaseModel):
    """管理员账号(账号管理列表)。不透出 password_hash / token_version / totp_secret。"""

    id: int
    username: str
    role: str
    status: str
    totp_enabled: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    password: str = Field(min_length=12, max_length=128)
    role: AdminRole
    reason: str = Field(min_length=2, max_length=200)


class AdminUpdateRequest(BaseModel):
    role: AdminRole | None = None
    status: Literal["active", "disabled"] | None = None
    reason: str = Field(min_length=2, max_length=200)


class AdminResetPasswordRequest(BaseModel):
    password: str = Field(min_length=12, max_length=128)
    reason: str = Field(min_length=2, max_length=200)


class AdminSelfPasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class MfaChallengeOut(BaseModel):
    """登录响应·挑战分支(admin_mfa_enabled 开启时):mfa_setup=首次绑定(绑定票 10min);
    mfa_required=已绑定验证(二要素票 5min)。"""

    status: Literal["mfa_setup", "mfa_required"]
    ticket: str


class AdminLoginTokenOut(BaseModel):
    """登录响应·直发分支(admin_mfa_enabled 关闭时)。"""

    status: Literal["ok"]  # 前端按 status 判别联合类型
    access_token: str
    admin: AdminOut


class MfaTicketRequest(BaseModel):
    ticket: str = Field(min_length=1)


class MfaSetupOut(BaseModel):
    """TOTP 绑定材料:otpauth_uri 渲染二维码;secret 供手动录入。"""

    secret: str
    otpauth_uri: str


class MfaCodeRequest(BaseModel):
    ticket: str = Field(min_length=1)
    code: str = Field(min_length=6, max_length=16)  # 6 位 TOTP 或 11 位恢复码(XXXXX-XXXXX)


class MfaSetupConfirmOut(BaseModel):
    """绑定成功:恢复码仅此一次返回,10 个。"""

    access_token: str
    admin: AdminOut
    recovery_codes: list[str]


class MfaLoginOut(BaseModel):
    """二要素验证通过。recovery_codes_left ≤2 提示重新生成。"""

    access_token: str
    admin: AdminOut
    recovery_codes_left: int | None = None


class RecoveryCodesOut(BaseModel):
    recovery_codes: list[str]


class MfaResetRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=REASON_MAX_LENGTH)


class AdminRefreshRequest(BaseModel):
    access_token: str = Field(min_length=1)


class AdminRefreshOut(BaseModel):
    access_token: str


# ---------- 管理端响应模型 ----------
# 金额一律 str(禁 float);时间为 isoformat 字符串。


class TenantOut(BaseModel):
    id: int
    phone_masked: str
    status: str
    balance: str
    total_consumed: str
    instances: int
    disk_gb: int
    created_at: str
    # 实名信息透出:readonly 角色脱敏;ops/finance/admin 明文(响应含实名字段即落审计)
    verification_status: str = "unverified"
    id_name: str | None = None


class TenantQuotaOut(BaseModel):
    """租户配额覆盖与生效值(override → policy → env)。三项 override 为 None = 走默认链。"""

    user_id: int
    max_gpus: int | None
    max_instances: int | None
    max_disks: int | None
    effective_max_gpus: int
    effective_max_instances: int
    effective_max_disks: int
    note: str | None = None
    updated_by: int | None = None
    updated_at: str | None = None


class TenantQuotaUpdate(BaseModel):
    """写覆盖:数字可留空(=该维走默认);全空 = 清除覆盖。note 必填。"""

    max_gpus: int | None = Field(default=None, ge=1, le=100000)
    max_instances: int | None = Field(default=None, ge=1, le=100000)
    max_disks: int | None = Field(default=None, ge=1, le=100000)
    note: str = Field(min_length=2, max_length=200)


class TenantStatusOut(BaseModel):
    id: int
    status: str
    # 仅冻结时返回:本次一并停掉的 running 实例台数
    instances_stopped: int | None = None


class OverviewPoolOut(BaseModel):
    """池级 GPU 台账:总量含非 Ready 节点。"""

    pool: str
    gpu_total: int
    gpu_used: int
    # 已租中属于竞价实例的卡数,按 gpu_used 截断
    gpu_spot_used: int
    ready_gpu_total: int


class OverviewOut(BaseModel):
    """总览聚合:全部精确计数。"""

    instances_by_status: dict[str, int]  # 非终态分状态计数(不含 released)
    tenants_total: int  # active 用户口径
    paying_tenants: int  # ledger consume > 0 的精确人数
    subscriptions_active: int  # 在保(未到期)的包周期实例数
    nodes_total: int
    nodes_ready: int
    nodes_missing: int
    pools: list[OverviewPoolOut]


class AdjustContextOut(BaseModel):
    """调账前置上下文:回显租户身份与资金现状。"""

    user_id: int
    phone_masked: str
    status: str
    balance: str
    running_instances: int
    recent_ledger: list[LedgerEntryOut]


class SkuImpactOut(BaseModel):
    """改价影响面:该 SKU 活跃(creating/starting/running)实例数/用户数/卡数。"""

    sku_id: int
    active_instances: int
    active_users: int
    active_gpus: int


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
    # 告警闭环:确认留痕 + 跳转目标
    acked_by: int | None = None
    acked_by_username: str | None = None
    acked_at: str | None = None
    target_kind: str | None = None  # tenant / node / ticket
    target_id: str | None = None


class AlertUnreadCountOut(BaseModel):
    """未确认告警数;critical_count 单独给出(精确口径)。"""

    count: int
    critical_count: int = 0


class AnnouncementOut(BaseModel):
    id: int
    title: str
    content: str
    status: str  # published / revoked
    reached: int
    created_by: int
    created_at: str
    revoked_at: str | None = None
    revoke_reason: str | None = None


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
    # 原因、变更前后值、金额都在这里。detail 禁止落凭据明文(平台配置只落键名不落值)。
    detail: dict[str, Any] | None = None
    created_at: str


class RevenueReportOut(BaseModel):
    """收入口径:`*_revenue` = 计量出账(按量 + 盘费,按账单归属期)+ 包周期预付(按收款当日);
    `*_prepaid` 是其中的预付部分。
    """

    today_revenue: str
    yesterday_revenue: str
    month_revenue: str
    today_prepaid: str
    month_prepaid: str
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
    group: PlatformConfigGroup
    kind: PlatformConfigKind
    choices: list[str]
    hint: str
    source: Literal["override", "env", "unset"]
    configured: bool
    value: str | None
    preview: str | None
    updated_at: str | None


class PlatformConfigWarningOut(BaseModel):
    key: str
    level: Literal["error", "warning"]
    message: str


class PlatformConfigOut(BaseModel):
    items: list[PlatformConfigItemOut]
    warnings: list[PlatformConfigWarningOut]


class SmsTestOut(BaseModel):
    ok: bool
    provider: str


class RegistryTestOut(BaseModel):
    """Harbor 连通性探测结果:step 指出失败步骤(health=DNS/TLS/CA 或 Harbor 自检,
    project=机器人鉴权/权限/项目存在性)。"""

    ok: bool
    step: Literal["health", "project", "done"]
    detail: str
    harbor_version: str | None
    repositories: int | None


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
    kind: Literal[
        "lost_callback", "closed_order", "failed_order", "channel_reversed", "negative_balance"
    ]
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
    """SKU 表单容量预览(纯台账推算)。"""

    matching_nodes: int
    ready_gpus: int  # CPU 规格恒 0
    total_gpus: int  # 同上
    # 共享档 = ready_gpus × ⌊100×oversell/pct⌋;dedicated/mig = ready_gpus;
    # CPU 规格 = 按节点 vCPU/内存上限折算(catalog.sellable_cpu_slots)
    est_instances: int
    warnings: list[CapacityWarningOut]
