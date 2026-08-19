from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.core.money import MoneyOut


class WalletOut(BaseModel):
    balance: MoneyOut
    frozen_amount: MoneyOut

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
    real_name_required_for_recharge: bool = False
    low_balance_warn_hours_default: int


class DailySummaryOut(BaseModel):
    """当日消费汇总(本地日界由 tz_offset_minutes 折算)。"""

    date: str
    gpu_total: MoneyOut
    disk_total: MoneyOut
    items: list[BillSummaryItem]


class RechargeCreate(BaseModel):
    amount: Decimal
    channel: str = "mock"  # wechat / alipay / mock(dev)


class RechargeOut(BaseModel):
    order_no: str
    amount: MoneyOut
    channel: str
    status: str
    qr_url: str | None
    expires_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}
