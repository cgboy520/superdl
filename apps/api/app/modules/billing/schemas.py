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
    # 展示用冗余(当前实例名;释放后仍可查,改名跟当前名),不参与对账
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
    """计费/回收策略常量(公开只读;前端展示口径的唯一来源,禁止前端硬编码)。"""

    disk_price_gb_month: MoneyOut
    disk_min_gb: int
    disk_max_gb: int
    disk_grace_days: int
    disk_frozen_days: int
    freeze_grace_hours: int
    # 包周期折扣(百分数,80 = 8 折)与到期预警窗。市场页的「包月 -20%」直接读这里 ——
    # 前端硬编码折扣就意味着运营在管理端调完价、页面还显示旧折扣
    period_discount_day: int
    period_discount_week: int
    period_discount_month: int
    period_discount_year: int
    period_expire_warn_days: int
    # 竞价折扣与抢占宽限窗:市场页的「低至 4 折」与知情同意里的「提前 60 秒通知」
    # 都从这里读,前端硬编码就意味着运营调完、页面还显示旧数
    spot_discount_pct: int
    spot_grace_seconds: int
    real_name_enabled: bool = False  # 用户端实名表单是否可用(安全策略开关)
    real_name_required_for_recharge: bool = False


class SubscriptionQuoteOut(BaseModel):
    """包周期报价。金额三件套由后端算好逐行下发,前端不自己做乘法 ——
    4 位单价 × 8760 小时的舍入差在前端算会和实扣金额对不齐。"""

    period: str
    period_count: int
    hours: int
    discount_pct: int
    base_hourly: MoneyOut  # SKU 原价时价
    unit_price: MoneyOut  # 折后时价
    list_amount: MoneyOut  # 原价总额
    discount_amount: MoneyOut  # 优惠额
    amount: MoneyOut  # 应付(实扣)


class SubscriptionOut(BaseModel):
    """包周期订阅明细(续费响应与账单页下钻用)。"""

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
    """当日消费汇总(本地日界由 tz_offset_minutes 折算)。"""

    date: str
    gpu_total: MoneyOut
    disk_total: MoneyOut
    items: list[BillSummaryItem]


# 充值金额上下限:只在契约层(pydantic,进 OpenAPI)校验一次
MIN_RECHARGE = Decimal("1.00")
MAX_RECHARGE = Decimal("50000.00")


class RechargeCreate(BaseModel):
    # 先量化后校验会让 1e30 这类值在 as_amount() 抛 InvalidOperation 漏成 500;
    # 契约层边界直接 422,且进 OpenAPI 契约
    amount: Decimal = Field(ge=MIN_RECHARGE, le=MAX_RECHARGE)
    channel: str  # wechat / alipay / mock(dev);必填:渠道须显式选择,不默认兜底


class RechargeOut(BaseModel):
    order_no: str
    amount: MoneyOut
    channel: str
    status: str
    qr_url: str | None
    expires_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------- 退款 ----------

# 线下打款渠道;不做渠道原路退回
PayoutChannel = Literal["offline", "alipay_transfer", "wechat_transfer"]


class RefundCreate(BaseModel):
    order_no: str = Field(min_length=4, max_length=40)
    # 契约层先挡负数/超大值(参照 RechargeCreate 注释);≤ min(订单额,余额) 在服务层校验
    amount: Decimal = Field(gt=0, le=MAX_RECHARGE)
    reason: str = Field(min_length=2, max_length=256)


class RefundOut(BaseModel):
    """用户端退款单视图。不透出 review_by/payout_by(操作人 id 对用户无意义)。"""

    id: int
    refund_no: str
    order_no: str
    amount: MoneyOut
    reason: str
    status: str
    review_comment: str | None  # 驳回理由/审批意见
    payout_channel: str | None
    payout_ref: str | None
    payout_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class RefundableOrderOut(BaseModel):
    """可申请退款口径的充值订单(用户端退款表单的数据源)。

    refundable=False 时 reason_code 说明置灰原因:
    not_paid(未支付)/ already_applied(已有活跃申请)/ invoiced(已开票,先红冲)/
    no_balance(当前余额为 0,无款可退)。
    """

    order_no: str
    amount: MoneyOut
    channel: str
    status: str
    paid_at: datetime | None
    refundable: bool
    reason_code: str | None
    max_amount: MoneyOut  # min(订单金额, 当前钱包余额)


class AdminRefundOut(RefundOut):
    """管理端退款单视图:比用户端多双人制衡的操作人/时间与核销流水关联。"""

    user_id: int
    review_by: int | None
    review_at: datetime | None
    payout_by: int | None
    wallet_entry_id: int | None


class RefundReview(BaseModel):
    approve: bool
    comment: str = Field(min_length=2, max_length=256)  # 同意/驳回都须填意见


class RefundPayout(BaseModel):
    channel: PayoutChannel
    ref: str = Field(min_length=2, max_length=128)  # 线下打款凭证号


class RefundCancel(BaseModel):
    reason: str = Field(min_length=2, max_length=256)


# ---------- 发票 ----------

InvoiceTitleType = Literal["personal", "company"]

# 账期 YYYY-MM(北京月界);能否申请(须 < 当前北京月)在服务层判定
INVOICE_PERIOD_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
# 宽松的邮箱格式校验(契约层挡明显畸形;真实可达性由开票人工核对)
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class InvoiceCreate(BaseModel):
    """开票申请。amount 不进契约:服务端按账期计算,客户端只提交账期+抬头(防篡改)。"""

    period: str = Field(pattern=INVOICE_PERIOD_PATTERN)
    title_type: InvoiceTitleType
    title: str = Field(min_length=2, max_length=128)
    tax_id: str | None = Field(default=None, min_length=4, max_length=32)
    email: str = Field(pattern=EMAIL_PATTERN, max_length=128)

    @field_validator("title", "tax_id", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v

    @model_validator(mode="after")
    def _company_needs_tax_id(self) -> "InvoiceCreate":
        if self.title_type == "company" and not self.tax_id:
            raise ValueError("tax_id is required for company title")
        if self.title_type == "personal":
            self.tax_id = None  # 个人抬头无税号:忽略入参,不落库
        return self


class InvoiceOut(BaseModel):
    """用户端发票申请视图。不透出 issued_by(操作人 id 对用户无意义)。"""

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
    """管理端发票申请视图:比用户端多租户 id 与开票操作人/时间。"""

    user_id: int
    issued_by: int | None
    issued_at: datetime | None


class InvoiceEligibleOut(BaseModel):
    """账期可开票额度预览项(仅 amount > 0 的账期)。"""

    period: str
    amount: MoneyOut


class InvoiceIssue(BaseModel):
    invoice_no: str = Field(min_length=2, max_length=64)  # 发票号(人工开票后回填)


class InvoiceReject(BaseModel):
    reason: str = Field(min_length=2, max_length=256)


# ---------- 结算缺口(管理端) ----------


class AdminSettlementGapOut(BaseModel):
    """管理端结算缺口视图:水位线被越过但账未结清的窗口留痕。"""

    id: int
    kind: str  # hourly / daily_disk
    window_start: datetime  # 缺口窗口起点(小时/自然日)
    object_id: int  # 实例/盘 id;0 = 整窗(截断/水位线丢失)
    reason: str  # catchup_truncated / dead_letter / watermark_missing / grace_overlap
    resolved_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class SettlementGapResolve(BaseModel):
    """人工核销(不重放):对象已不存在/grace_overlap 确认无账时的出口。说明必填。"""

    note: str = Field(min_length=2, max_length=256)  # 核销说明(审计留痕)
