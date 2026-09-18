from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.money import MoneyOut


class WalletOut(BaseModel):
    balance: MoneyOut

    model_config = {"from_attributes": True}


class LedgerEntryOut(BaseModel):
    id: int
    type: str
    amount: MoneyOut
    balance_after: MoneyOut
    ref_type: str | None
    ref_id: str | None
    remark: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class BillHourlyOut(BaseModel):
    id: int
    instance_id: int
    instance_name: str | None = None
    hour_start: datetime
    seconds_used: int
    unit_price: MoneyOut
    gpu_count: int
    amount: MoneyOut

    model_config = {"from_attributes": True}


class BillSummaryItem(BaseModel):
    instance_id: int
    instance_name: str | None = None
    total_amount: MoneyOut
    total_seconds: int


class BillSummaryOut(BaseModel):
    month: str
    gpu_total: MoneyOut
    disk_total: MoneyOut
    items: list[BillSummaryItem]


class PoliciesOut(BaseModel):
    """Public read-only billing and reclamation policies."""

    disk_price_gb_month: MoneyOut
    disk_min_gb: int
    disk_max_gb: int
    disk_grace_days: int
    disk_frozen_days: int
    freeze_grace_hours: int
    period_discount_day: int
    period_discount_week: int
    period_discount_month: int
    period_discount_year: int
    period_expire_warn_days: int
    spot_discount_pct: int
    spot_grace_seconds: int
    real_name_enabled: bool = False
    real_name_required_for_recharge: bool = False
    currency: str
    billing_timezone: str
    recharge_min: MoneyOut
    recharge_max: MoneyOut
    recharge_presets: list[MoneyOut]


class SubscriptionQuoteOut(BaseModel):
    """Subscription quote with the server-computed list price, discount and payable amount."""

    period: str
    period_count: int
    hours: int
    discount_pct: int
    base_hourly: MoneyOut
    unit_price: MoneyOut
    list_amount: MoneyOut
    discount_amount: MoneyOut
    amount: MoneyOut


class SubscriptionOut(BaseModel):
    """Subscription detail."""

    id: int
    instance_id: int
    period: str
    period_count: int
    unit_price: MoneyOut
    amount_paid: MoneyOut
    started_at: datetime
    expires_at: datetime
    status: str
    auto_renew: bool

    model_config = {"from_attributes": True}


class DailySummaryOut(BaseModel):
    """Today's consumption summary (local day boundary via tz_offset_minutes, default the billing
    zone's current offset)."""

    date: str
    gpu_total: MoneyOut
    disk_total: MoneyOut
    items: list[BillSummaryItem]


#: Contract-level sanity cap; the business bounds are the `recharge_min` / `recharge_max` policies.
AMOUNT_HARD_CAP = Decimal("100000000")


class RechargeCreate(BaseModel):
    amount: Decimal = Field(gt=0, le=AMOUNT_HARD_CAP)
    channel: str


class RechargeOut(BaseModel):
    """`presentation` tells the console how to show `payment_url`: `qr` renders it as a QR code,
    `redirect` sends the payer to it (checkout page) and resumes on `/billing?recharge=`."""

    order_no: str
    amount: MoneyOut
    currency: str
    channel: str
    presentation: Literal["qr", "redirect"]
    status: str
    payment_url: str | None
    expires_at: datetime
    created_at: datetime


PayoutChannel = Literal["offline", "alipay_transfer", "wechat_transfer"]


class RefundCreate(BaseModel):
    order_no: str = Field(min_length=4, max_length=40)
    amount: Decimal = Field(gt=0, le=AMOUNT_HARD_CAP)
    reason: str = Field(min_length=2, max_length=256)


class RefundOut(BaseModel):
    """User refund request view. review_by/payout_by are not exposed."""

    id: int
    refund_no: str
    order_no: str
    amount: MoneyOut
    reason: str
    status: str
    review_comment: str | None
    payout_channel: str | None
    payout_ref: str | None
    payout_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class RefundableOrderOut(BaseModel):
    """Top-up order under the refund-eligibility definition.

    With refundable=False, reason_code: not_paid / already_applied / fully_refunded / invoiced
    / no_balance.
    An order may be partially refunded several times: max_amount = min(order remainder,
    non-negative available balance, refundable ledger balance).
    Remainder = order amount − Σ paid refunds; available balance = balance − frozen.
    The refundable ledger balance is the net ledger excluding positive adjustments, floored at
    zero.
    """

    order_no: str
    amount: MoneyOut
    channel: str
    status: str
    paid_at: datetime | None
    refundable: bool
    reason_code: str | None
    max_amount: MoneyOut


class AdminRefundOut(RefundOut):
    """Admin refund request view: operators / times and the write-off ledger link;
    `order_payout_channel` is the registry's payout counterpart of `order_channel` (None = offline
    only)."""

    user_id: int
    order_channel: str | None = None
    order_payout_channel: str | None = None
    review_by: int | None
    review_at: datetime | None
    payout_by: int | None
    wallet_entry_id: int | None


class RefundReview(BaseModel):
    approve: bool
    comment: str = Field(min_length=2, max_length=256)


class RefundPayout(BaseModel):
    channel: PayoutChannel
    ref: str = Field(min_length=2, max_length=128)


class RefundCancel(BaseModel):
    reason: str = Field(min_length=2, max_length=256)


InvoiceTitleType = Literal["personal", "company"]

INVOICE_PERIOD_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class InvoiceCreate(BaseModel):
    """Invoice request. amount is not part of the contract: computed server-side per period."""

    period: str = Field(pattern=INVOICE_PERIOD_PATTERN)
    title_type: InvoiceTitleType
    title: str = Field(min_length=2, max_length=128)
    tax_id: str | None = Field(default=None, max_length=32)
    email: str = Field(pattern=EMAIL_PATTERN, max_length=128)

    @field_validator("title", "tax_id", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v

    @model_validator(mode="after")
    def _company_needs_tax_id(self) -> "InvoiceCreate":
        """Company titles need a tax ID (2–32 chars); its format is the compliance profile's rule,
        checked by the service. Personal titles never carry one."""
        if self.title_type == "company":
            if not self.tax_id:
                raise ValueError("tax_id is required for company title")
            if len(self.tax_id) < 2:
                raise ValueError("tax_id must be at least 2 characters")
        if self.title_type == "personal":
            self.tax_id = None
        return self


class InvoiceOut(BaseModel):
    """User invoice request view. issued_by is not exposed."""

    id: int
    period: str
    title_type: str
    title: str
    tax_id: str | None
    email: str
    amount: MoneyOut
    status: str
    invoice_no: str | None
    reject_reason: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminInvoiceOut(InvoiceOut):
    """Admin invoice request view: with the tenant id and the issuing operator / time."""

    user_id: int
    issued_by: int | None
    issued_at: datetime | None


class InvoiceEligibleOut(BaseModel):
    """Invoiceable amount preview item (periods with amount > 0 only)."""

    period: str
    amount: MoneyOut


class InvoiceIssue(BaseModel):
    invoice_no: str = Field(min_length=2, max_length=64)


class InvoiceReject(BaseModel):
    reason: str = Field(min_length=2, max_length=256)


class AdminSettlementGapOut(BaseModel):
    """Admin settlement gap view."""

    id: int
    kind: str
    window_start: datetime
    object_id: int
    reason: str
    resolved_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class SettlementGapResolve(BaseModel):
    """Manual write-off (no replay). A note is required."""

    note: str = Field(min_length=2, max_length=256)
