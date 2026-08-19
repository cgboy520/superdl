from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Wallet(Base):
    """余额与冻结额。更新必须 SELECT FOR UPDATE + 同事务写 ledger。"""

    __tablename__ = "wallets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(unique=True)
    balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    frozen_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class BalanceLedger(Base):
    """追加式资金流水,对账基准。amount 带符号;balance_after 为扣/入账后的快照。"""

    __tablename__ = "balance_ledger"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    type: Mapped[str] = mapped_column(String(16))  # recharge / consume / refund / adjust
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    balance_after: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    ref_type: Mapped[str | None] = mapped_column(String(32))  # bill_hourly / order / adjustment...
    ref_id: Mapped[str | None] = mapped_column(String(64))
    remark: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)


class BillHourly(Base):
    """实例小时账单。UNIQUE(instance_id, hour_start) 即结算幂等键。"""

    __tablename__ = "bills_hourly"
    __table_args__ = (UniqueConstraint("instance_id", "hour_start"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    instance_id: Mapped[int] = mapped_column(index=True)
    user_id: Mapped[int] = mapped_column(index=True)
    hour_start: Mapped[datetime] = mapped_column(index=True)
    seconds_used: Mapped[int]
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    gpu_count: Mapped[int] = mapped_column(default=1)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    detail: Mapped[dict | None] = mapped_column(JSONB)  # 事件重建的时段明细,便于争议核查
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class BillDailyDisk(Base):
    """数据盘日结。UNIQUE(disk_id, day) 幂等;关机也扣(「日常费用」)。"""

    __tablename__ = "bills_daily_disk"
    __table_args__ = (UniqueConstraint("disk_id", "day"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    disk_id: Mapped[int] = mapped_column(index=True)
    user_id: Mapped[int] = mapped_column(index=True)
    day: Mapped[datetime]
    size_gb: Mapped[int]
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 4))  # 元/GB·月
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Order(Base):
    """充值订单。支付回调幂等靠 channel_txn_id 唯一 + status 检查。"""

    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(40), unique=True)
    user_id: Mapped[int] = mapped_column(index=True)
    type: Mapped[str] = mapped_column(String(16), default="recharge")
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    channel: Mapped[str] = mapped_column(String(16))  # wechat / alipay / mock
    channel_txn_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # pending / paid / closed / failed
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    qr_url: Mapped[str | None] = mapped_column(String(512))
    paid_at: Mapped[datetime | None]
    expires_at: Mapped[datetime]
    # 发票字段预留(MVP 不做开票流程)
    invoice_title: Mapped[str | None] = mapped_column(String(128))
    invoice_tax_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
