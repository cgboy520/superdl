from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Index,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.money import platform_currency


class Wallet(Base):
    """余额。更新必须 SELECT FOR UPDATE + 同事务写 ledger。

    frozen:渠道冲正冻结额;可用余额 = balance - frozen;冻结不记 ledger,
    核销时 release 或 chargeback。"""

    __tablename__ = "wallets"
    __table_args__ = (CheckConstraint("frozen >= 0", name="frozen_nonneg"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(unique=True)
    balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    frozen: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class BalanceLedger(Base):
    """追加式资金流水,对账基准。amount 带符号;balance_after 为扣/入账后的快照。"""

    __tablename__ = "balance_ledger"
    __table_args__ = (
        CheckConstraint("amount <> 0", name="amount_nonzero"),
        CheckConstraint(
            "ref_type IS NULL OR ref_type IN"
            " ('bill_hourly', 'bill_daily_disk', 'order', 'adjustment', 'refund_request',"
            " 'subscription')",
            name="ref_type",
        ),
        Index("ix_balance_ledger_user_id_id", "user_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    type: Mapped[str] = mapped_column(String(16))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    balance_after: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    ref_type: Mapped[str | None] = mapped_column(String(32))
    ref_id: Mapped[str | None] = mapped_column(String(64))
    remark: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class BillHourly(Base):
    """实例小时账单。UNIQUE(instance_id, hour_start) 即结算幂等键。"""

    __tablename__ = "bills_hourly"
    __table_args__ = (
        UniqueConstraint("instance_id", "hour_start"),
        CheckConstraint("seconds_used >= 0 AND seconds_used <= 3600", name="seconds_range"),
        CheckConstraint("amount >= 0", name="amount_nonneg"),
        Index("ix_bills_hourly_user_hour", "user_id", "hour_start"),
        Index("ix_bills_hourly_user_id_id", "user_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    instance_id: Mapped[int]
    user_id: Mapped[int] = mapped_column(index=True)
    hour_start: Mapped[datetime] = mapped_column(index=True)
    seconds_used: Mapped[int]
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    gpu_count: Mapped[int] = mapped_column(default=1)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    detail: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class BillDailyDisk(Base):
    """数据盘日结。UNIQUE(disk_id, day) 幂等;关机也扣。"""

    __tablename__ = "bills_daily_disk"
    __table_args__ = (
        UniqueConstraint("disk_id", "day"),
        CheckConstraint("amount >= 0", name="amount_nonneg"),
        CheckConstraint("size_gb >= 0", name="size_nonneg"),
        Index("ix_bills_daily_disk_user_day", "user_id", "day"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    disk_id: Mapped[int]
    user_id: Mapped[int] = mapped_column(index=True)
    day: Mapped[datetime]
    size_gb: Mapped[int]
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class SettlementWatermark(Base):
    """已处理的最后一个窗口起点;hourly 为小时,daily_disk 为自然日,未结清窗口另记缺口。"""

    __tablename__ = "settlement_watermarks"

    key: Mapped[str] = mapped_column(String(16), primary_key=True)
    settled_through: Mapped[datetime]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class SettlementGap(Base):
    """结算缺口登记:水位线越过但账未结清的窗口。

    四种来源:catchup_truncated(整窗,object_id=0)/ dead_letter(单对象)/ watermark_missing(整窗)
    / grace_overlap(单盘)。只登记不自动补,处理后标记 resolved_at。
    """

    __tablename__ = "settlement_gaps"
    __table_args__ = (UniqueConstraint("kind", "window_start", "object_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    window_start: Mapped[datetime]
    object_id: Mapped[int] = mapped_column(BigInteger, default=0)
    reason: Mapped[str] = mapped_column(String(32))
    resolved_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ReconcileCheckpoint(Base):
    """用户钱包核对游标:最后核验的流水 id、余额快照及核验时间。"""

    __tablename__ = "reconcile_checkpoints"

    user_id: Mapped[int] = mapped_column(primary_key=True)
    last_ledger_id: Mapped[int] = mapped_column(BigInteger, default=0)
    balance_after: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    updated_at: Mapped[datetime]


class Order(Base):
    """充值订单。支付回调幂等靠 channel_txn_id 唯一 + status 检查。"""

    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        UniqueConstraint("backfill_idempotency_key"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("status IN ('pending', 'paid', 'failed', 'closed')", name="status"),
        CheckConstraint(
            "channel_reversal_action IS NULL "
            "OR channel_reversal_action IN ('release', 'chargeback')",
            name="reversal_action",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(40), unique=True)
    user_id: Mapped[int] = mapped_column(index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default=platform_currency)
    channel: Mapped[str] = mapped_column(String(16))
    channel_txn_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    backfill_idempotency_key: Mapped[str | None] = mapped_column(String(64))
    payment_url: Mapped[str | None] = mapped_column(String(2048))
    channel_ref: Mapped[str | None] = mapped_column(String(128))
    paid_at: Mapped[datetime | None]
    channel_reversed_at: Mapped[datetime | None]
    channel_reversal_resolved_at: Mapped[datetime | None]
    channel_reversal_action: Mapped[str | None] = mapped_column(String(16))
    expires_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class BillingIdentity(Base):
    """Single row (id = 1) locking the deployment's currency and billing timezone; written on
    first boot by `billing.identity.assert_billing_identity`."""

    __tablename__ = "billing_identity"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    currency: Mapped[str] = mapped_column(String(3))
    timezone: Mapped[str] = mapped_column(String(64))
    locked_at: Mapped[datetime]


def reversal_pending(order: "Order") -> bool:
    """渠道冲正待处置:已收到反向通知且尚未核销。"""
    return order.channel_reversed_at is not None and order.channel_reversal_resolved_at is None


def reversal_blocks_refund(order: "Order") -> bool:
    """待处置或已坐实(chargeback)的冲正都禁止平台侧再出金;release 过的订单恢复资格。"""
    return order.channel_reversed_at is not None and order.channel_reversal_action != "release"


class InvoiceRequest(Base):
    """按北京自然月计算金额的发票申请;每用户每账期至多一条 submitted/issued 申请。"""

    __tablename__ = "invoice_requests"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("status IN ('submitted', 'issued', 'rejected')", name="status"),
        CheckConstraint("title_type IN ('personal', 'company')", name="title_type"),
        Index(
            "uq_invoice_requests_active_period",
            "user_id",
            "period",
            unique=True,
            postgresql_where=text("status IN ('submitted', 'issued')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    period: Mapped[str] = mapped_column(String(7))
    title_type: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(128))
    tax_id: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str] = mapped_column(String(128))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(16), default="submitted", index=True)
    invoice_no: Mapped[str | None] = mapped_column(String(64))
    reject_reason: Mapped[str | None] = mapped_column(String(256))
    issued_by: Mapped[int | None]
    issued_at: Mapped[datetime | None]
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class RefundRequest(Base):
    """退款申请;每订单至多一条 pending/approved,打款人与审批人须不同。

    登记打款时同事务扣款并回写 wallet_entry_id;累计退款不得超过订单额。
    """

    __tablename__ = "refund_requests"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'paid', 'cancelled')", name="status"
        ),
        CheckConstraint(
            "payout_by IS NULL OR review_by IS NULL OR payout_by <> review_by",
            name="payout_not_reviewer",
        ),
        Index(
            "uq_refund_requests_active_order",
            "order_no",
            unique=True,
            postgresql_where=text("status IN ('pending', 'approved')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    refund_no: Mapped[str] = mapped_column(String(20), unique=True)
    user_id: Mapped[int] = mapped_column(index=True)
    order_no: Mapped[str] = mapped_column(String(40))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    reason: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    review_by: Mapped[int | None]
    review_at: Mapped[datetime | None]
    review_comment: Mapped[str | None] = mapped_column(String(256))
    payout_channel: Mapped[str | None] = mapped_column(String(32))
    payout_ref: Mapped[str | None] = mapped_column(String(128))
    payout_by: Mapped[int | None]
    payout_at: Mapped[datetime | None]
    wallet_entry_id: Mapped[int | None] = mapped_column(BigInteger)
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    payout_idempotency_key: Mapped[str | None] = mapped_column(String(64))
    payout_request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Subscription(Base):
    """包周期预付订单;每实例至多一条 active,续费新建行并由 renewed_from_id 关联旧行。"""

    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        CheckConstraint("period IN ('day', 'week', 'month', 'year')", name="period"),
        CheckConstraint("period_count >= 1", name="period_count_positive"),
        CheckConstraint("amount_paid >= 0", name="amount_nonneg"),
        CheckConstraint(
            "status IN ('active', 'expired', 'cancelled')",
            name="status",
        ),
        Index(
            "ix_subscriptions_active_expiry",
            "expires_at",
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "uq_subscriptions_active_instance",
            "instance_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    instance_id: Mapped[int] = mapped_column(index=True)
    sku_id: Mapped[int]
    period: Mapped[str] = mapped_column(String(8))
    period_count: Mapped[int] = mapped_column(default=1)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    started_at: Mapped[datetime]
    expires_at: Mapped[datetime] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    auto_renew: Mapped[bool] = mapped_column(default=False, server_default="false")
    renewed_from_id: Mapped[int | None]
    warned_for_expiry: Mapped[datetime | None]
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
