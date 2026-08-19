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
