"""Admin API models; the audit detail never carries credential plaintext, platform config records
key names only."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.platform_config import PlatformConfigGroup, PlatformConfigKind
from app.core.security import PasswordStr
from app.modules.billing.schemas import LedgerEntryOut, RechargeOut


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class AdminOut(BaseModel):
    id: int
    username: str
    role: str

    model_config = {"from_attributes": True}


AdminRole = Literal["admin", "ops", "finance", "readonly"]

REASON_MAX_LENGTH = 256


class AdminAccountOut(BaseModel):
    """Admin account (account management list). password_hash / token_version / totp_secret are
    never exposed."""

    id: int
    username: str
    role: str
    status: str
    totp_enabled: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    password: PasswordStr
    role: AdminRole
    reason: str = Field(min_length=2, max_length=200)


class AdminUpdateRequest(BaseModel):
    role: AdminRole | None = None
    status: Literal["active", "disabled"] | None = None
    reason: str = Field(min_length=2, max_length=200)


class AdminResetPasswordRequest(BaseModel):
    password: PasswordStr
    reason: str = Field(min_length=2, max_length=200)


class AdminSelfPasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: PasswordStr


class MfaChallengeOut(BaseModel):
    """Login response, challenge branch (admin_mfa_enabled on): mfa_setup = first enrolment
    (enrolment ticket 10 min);
    mfa_required = enrolled verification (second-factor ticket 5 min)."""

    status: Literal["mfa_setup", "mfa_required"]
    ticket: str


class AdminLoginTokenOut(BaseModel):
    """Login response, direct branch (admin_mfa_enabled off)."""

    status: Literal["ok"]
    access_token: str
    admin: AdminOut


class MfaTicketRequest(BaseModel):
    ticket: str = Field(min_length=1)


class MfaSetupOut(BaseModel):
    """TOTP enrolment material: otpauth_uri renders the QR code; secret for manual entry."""

    secret: str
    otpauth_uri: str


class MfaCodeRequest(BaseModel):
    ticket: str = Field(min_length=1)
    code: str = Field(min_length=6, max_length=16)


class MfaSetupConfirmOut(BaseModel):
    """Enrolment succeeded: recovery codes returned this once, 10 of them."""

    access_token: str
    admin: AdminOut
    recovery_codes: list[str]


class MfaLoginOut(BaseModel):
    """Second factor verified. recovery_codes_left ≤ 2 prompts regeneration."""

    access_token: str
    admin: AdminOut
    recovery_codes_left: int | None = None


class RecoveryCodesOut(BaseModel):
    recovery_codes: list[str]


class ReasonBody(BaseModel):
    """Request body carrying only the operator reason (into the audit detail); every admin "reason
    required" action derives from it."""

    reason: str = Field(min_length=2, max_length=REASON_MAX_LENGTH)


class MfaResetRequest(ReasonBody):
    pass


class AdminRefreshRequest(BaseModel):
    access_token: str = Field(min_length=1)


class AdminRefreshOut(BaseModel):
    access_token: str


class TenantOut(BaseModel):
    id: int
    email_masked: str | None
    phone_masked: str | None
    status: str
    balance: str
    total_consumed: str
    instances: int
    disk_gb: int
    created_at: str
    kyc_status: str = "unverified"
    kyc_name: str | None = None


class TenantQuotaOut(BaseModel):
    """Tenant quota overrides and effective values (override → policy → env). An override of None =
    the default chain."""

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
    """Write overrides: numbers may be empty (= that dimension follows the default); all empty =
    clear the override. note required."""

    max_gpus: int | None = Field(default=None, ge=1, le=100000)
    max_instances: int | None = Field(default=None, ge=1, le=100000)
    max_disks: int | None = Field(default=None, ge=1, le=100000)
    note: str = Field(min_length=2, max_length=200)


class TenantStatusOut(BaseModel):
    id: int
    status: str
    instances_stopped: int | None = None


class OverviewPoolOut(BaseModel):
    """Per-pool GPU inventory: totals include non-Ready nodes."""

    pool: str
    gpu_total: int
    gpu_used: int
    gpu_spot_used: int
    ready_gpu_total: int


class OverviewOut(BaseModel):
    """Overview aggregate: all exact counts."""

    instances_by_status: dict[str, int]
    tenants_total: int
    paying_tenants: int
    subscriptions_active: int
    nodes_total: int
    nodes_ready: int
    nodes_missing: int
    pools: list[OverviewPoolOut]


class AdjustContextOut(BaseModel):
    """Adjustment context: echoes the tenant identity and money state."""

    user_id: int
    email_masked: str | None
    phone_masked: str | None
    status: str
    balance: str
    running_instances: int
    recent_ledger: list[LedgerEntryOut]


class AdminAlertOut(BaseModel):
    id: int
    type: str
    title: str
    content: str
    severity: str
    created_at: str
    acked_by: int | None = None
    acked_by_username: str | None = None
    acked_at: str | None = None
    target_kind: str | None = None
    target_id: str | None = None


class AlertUnreadCountOut(BaseModel):
    """Unacknowledged alert count; critical_count given separately (exact)."""

    count: int
    critical_count: int = 0


class AnnouncementOut(BaseModel):
    id: int
    title: str
    content: str
    status: str
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
    detail: dict[str, Any] | None = None
    created_at: str


class RevenueReportOut(BaseModel):
    """Revenue definition: `*_revenue` = metered bills (on-demand + disk fees, by bill attribution
    period) + subscription prepayments (by payment day);
    `*_prepaid` is the prepaid part of it.
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
    min: str | None
    max: str | None


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


class DeploymentIdentityOut(BaseModel):
    """Deployment-level identity set by env, shown read-only on the platform-config page."""

    compliance_profile: str
    currency: str
    billing_timezone: str


class PlatformConfigOut(BaseModel):
    items: list[PlatformConfigItemOut]
    warnings: list[PlatformConfigWarningOut]
    deployment: DeploymentIdentityOut


class SmsTestOut(BaseModel):
    ok: bool
    provider: str


class EmailTestOut(BaseModel):
    ok: bool
    provider: str


class RegistryTestOut(BaseModel):
    """Harbor connectivity probe result: step names the failing step (health = DNS/TLS/CA or the
    Harbor health check,
    project = robot auth / permission / project existence)."""

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
    order_currency: str
    channel_status: str
    channel_txn_id: str | None
    channel_amount: str | None
    channel_currency: str | None
    matches: bool


class OrderBackfillOut(BaseModel):
    order_no: str
    status: str


class AdminOrderOut(RechargeOut):
    user_id: int
