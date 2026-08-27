from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.platform_config import SettingGroup, SettingKind
from app.modules.billing.schemas import LedgerEntryOut, RechargeOut


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    # 与创建/重置同标准(128):更短的登录上限会把 65~128 字符口令的管理员永久锁在门外
    password: str = Field(min_length=1, max_length=128)


class AdminOut(BaseModel):
    id: int
    username: str
    role: str

    model_config = {"from_attributes": True}


AdminRole = Literal["admin", "ops", "finance", "readonly"]

# 只含 reason 的高危操作请求体(撤回公告/忽略与重放死信/冻结解冻租户/删镜像/重置 MFA/补单/
# 停止恢复调度/吊销注册)共用的原因长度上限。不合并成一个 ReasonBody:orval 按 schema 名
# 生成前端类型,各自的名字有页面在引用。
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
    """登录响应·挑战分支(安全策略 admin_mfa_enabled 开启时,不直发 token):
    mfa_setup=首次绑定(绑定票 10min);mfa_required=已绑定验证(二要素票 5min)。"""

    status: Literal["mfa_setup", "mfa_required"]
    ticket: str


class AdminLoginTokenOut(BaseModel):
    """登录响应·直发分支(安全策略 admin_mfa_enabled 关闭时):密码校验通过即签发 access token。"""

    status: Literal["ok"]  # 必填:前端按 status 判别联合类型(可选字段无法收窄)
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
    """绑定成功:恢复码仅此一次返回,10 个,须离线保存。"""

    access_token: str
    admin: AdminOut
    recovery_codes: list[str]


class MfaLoginOut(BaseModel):
    """二要素验证通过。用了恢复码时 recovery_codes_left 骤减,≤2 提示重新生成。"""

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
    # 实名信息透出:readonly 角色脱敏;ops/finance/admin 明文(敏感读,响应含实名字段即落审计)
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
    """写覆盖:三个数字可留空(=该维走默认);全空 = 清除覆盖恢复默认。note 必填(留痕)。"""

    max_gpus: int | None = Field(default=None, ge=1, le=100000)
    max_instances: int | None = Field(default=None, ge=1, le=100000)
    max_disks: int | None = Field(default=None, ge=1, le=100000)
    note: str = Field(min_length=2, max_length=200)


class TenantStatusOut(BaseModel):
    id: int
    status: str
    # 仅冻结时返回:本次一并停掉的 running 实例台数(前端回显用)
    instances_stopped: int | None = None


class OverviewPoolOut(BaseModel):
    """池级 GPU 台账:总量含非 Ready 节点(前端单独画第三段)。"""

    pool: str
    gpu_total: int
    gpu_used: int
    ready_gpu_total: int


class OverviewOut(BaseModel):
    """总览聚合:全部精确计数,不从截断列表推算。"""

    instances_by_status: dict[str, int]  # 非终态分状态计数(不含 released)
    tenants_total: int  # active 用户口径
    paying_tenants: int  # ledger consume > 0 的精确人数
    subscriptions_active: int  # 在保(未到期)的包周期实例数,精确 COUNT
    nodes_total: int
    nodes_ready: int
    nodes_missing: int
    pools: list[OverviewPoolOut]


class AdjustContextOut(BaseModel):
    """调账前置上下文:回显租户身份与资金现状,防止调错人。"""

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
    gpu_model: str  # canonical;未识别时为 "GPU"(展示兜底)
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
    """台账按 canonical×池聚合(SKU「从集群资源创建」下拉数据源)。gpu_model=None 为未识别桶。"""

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
    # 告警闭环:确认留痕 + 跳转目标(无 target 前端不可点)
    acked_by: int | None = None
    acked_by_username: str | None = None
    acked_at: str | None = None
    target_kind: str | None = None  # tenant / node / ticket
    target_id: str | None = None


class AlertUnreadCountOut(BaseModel):
    """未确认告警数(顶栏铃铛角标)。"""

    count: int


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
    # 原因、变更前后值、金额都在这里。
    # 约束:set_audit_target 的 detail 禁止落凭据明文(平台配置只落键名不落值)。
    detail: dict[str, Any] | None = None
    created_at: str


class RevenueReportOut(BaseModel):
    """收入口径:`*_revenue` = 计量出账(按量 + 盘费,按账单归属期)+ 包周期预付(按收款当日)。

    `*_prepaid` 是其中的预付部分,单独给出来是因为一笔包年会在当天造成一个尖峰,
    看环比时必须能把它拆出来。
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
    group: SettingGroup
    kind: SettingKind
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
    """Harbor 连通性探测结果:step 指出失败发生在哪一步(health=DNS/TLS/CA 或 Harbor 自检,
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
    """SKU 表单容量预览(纯台账推算,不做库存预占)。"""

    matching_nodes: int
    ready_gpus: int  # CPU 规格恒 0(不带卡)
    total_gpus: int  # 同上
    # 共享档 = ready_gpus × ⌊100×oversell/pct⌋;dedicated/mig = ready_gpus;
    # CPU 规格 = 按节点 vCPU/内存上限折算(catalog.sellable_cpu_slots)
    est_instances: int
    warnings: list[CapacityWarningOut]


class ClusterComponentOut(BaseModel):
    """组件体检项:key 由前端映射文案;fix_hint 为可复制修复命令(不随语言)。"""

    key: Literal[
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
    ok: bool
    detail: str | None = None
    fix_hint: str | None = None


class ClusterConfigStateOut(BaseModel):
    """配置就绪位(不回明文):集群页据此指引去平台配置页补键。"""

    server_url_set: bool
    join_token_set: bool
    prometheus_url_set: bool
    grafana_url: str | None
    # 镜像仓库地址与平台项目(非密):镜像页新建表单的默认前缀,ops/readonly 可读
    registry_host: str | None
    registry_project: str | None


class ClusterStatusOut(BaseModel):
    """集群页数据(纯 DB 读能力缓存;「测试连接」同步探测后返回同形)。"""

    api_reachable: bool
    k8s_version: str | None
    distro: str | None
    probed_at: datetime | None
    pools: dict[str, int]
    components: list[ClusterComponentOut]
    config: ClusterConfigStateOut
    error: str | None
