from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


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
    # amount <> 0;不加 balance >= 0(允许透支)
    __table_args__ = (CheckConstraint("amount <> 0", name="amount_nonzero"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    type: Mapped[str] = mapped_column(String(16))  # recharge / consume / refund / adjust
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    balance_after: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    ref_type: Mapped[str | None] = mapped_column(String(32))  # bill_hourly / order / adjustment...
    ref_id: Mapped[str | None] = mapped_column(String(64))
    remark: Mapped[str | None] = mapped_column(String(256))
    # 翻页/对账走主键 id,created_at 不建索引
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class BillHourly(Base):
    """实例小时账单。UNIQUE(instance_id, hour_start) 即结算幂等键。"""

    __tablename__ = "bills_hourly"
    __table_args__ = (
        UniqueConstraint("instance_id", "hour_start"),
        # 单个自然小时窗口最多 3600 秒(兜住旁路写入)
        CheckConstraint("seconds_used >= 0 AND seconds_used <= 3600", name="seconds_range"),
        CheckConstraint("amount >= 0", name="amount_nonneg"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # 查询走 UniqueConstraint(instance_id, hour_start) 前导列
    instance_id: Mapped[int]
    user_id: Mapped[int] = mapped_column(index=True)
    hour_start: Mapped[datetime] = mapped_column(index=True)
    seconds_used: Mapped[int]
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    gpu_count: Mapped[int] = mapped_column(default=1)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    detail: Mapped[dict | None] = mapped_column(JSONB)  # 事件重建的时段明细
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class BillDailyDisk(Base):
    """数据盘日结。UNIQUE(disk_id, day) 幂等;关机也扣。"""

    __tablename__ = "bills_daily_disk"
    __table_args__ = (
        UniqueConstraint("disk_id", "day"),
        CheckConstraint("amount >= 0", name="amount_nonneg"),
        CheckConstraint("size_gb >= 0", name="size_nonneg"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    disk_id: Mapped[int]  # 查询走 UniqueConstraint(disk_id, day) 前导列
    user_id: Mapped[int] = mapped_column(index=True)
    day: Mapped[datetime]
    size_gb: Mapped[int]
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 4))  # 元/GB·月
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class SettlementWatermark(Base):
    """结算水位线:已结清的最后一个窗口起点(key='hourly' 小时,'daily_disk' 自然日)。
    结算口径是「从水位线追平到当前」。
    """

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
    kind: Mapped[str] = mapped_column(String(16))  # hourly / daily_disk
    window_start: Mapped[datetime]  # 缺口窗口起点(小时/自然日)
    object_id: Mapped[int] = mapped_column(BigInteger, default=0)  # 实例/盘 id;0 = 整窗
    # catchup_truncated / dead_letter / watermark_missing / grace_overlap
    reason: Mapped[str] = mapped_column(String(32))
    resolved_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ReconcileCheckpoint(Base):
    """钱包-流水链式核对的增量游标:该用户已验到的最后一笔流水及其钱包快照。
    updated_at 与 wallets.updated_at 比较决定下轮是否需重验。
    """

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
        # 人工补单幂等键 DB 兜底
        UniqueConstraint("backfill_idempotency_key"),
        CheckConstraint("amount > 0", name="amount_positive"),
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
    channel: Mapped[str] = mapped_column(String(16))  # wechat / alipay / mock
    channel_txn_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # pending / paid / closed / failed
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    # 请求体指纹 sha256(user_id|amount|channel):同键异参重放 409
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    # 管理端人工补单的幂等键:同键重放回当前状态
    backfill_idempotency_key: Mapped[str | None] = mapped_column(String(64))
    qr_url: Mapped[str | None] = mapped_column(String(512))
    paid_at: Mapped[datetime | None]
    # 渠道侧对已入账订单的关单/退款通知首次到达时刻(异常清单分桶依据);人工处置后写 resolved_at +
    # action,标记本身不清(清了会被重放的同一通知再冻一次)
    channel_reversed_at: Mapped[datetime | None]
    channel_reversal_resolved_at: Mapped[datetime | None]
    channel_reversal_action: Mapped[str | None] = mapped_column(String(16))  # release / chargeback
    expires_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


def reversal_pending(order: "Order") -> bool:
    """渠道冲正待处置:已收到反向通知且尚未核销。"""
    return order.channel_reversed_at is not None and order.channel_reversal_resolved_at is None


def reversal_blocks_refund(order: "Order") -> bool:
    """待处置或已坐实(chargeback)的冲正都禁止平台侧再出金;release 过的订单恢复资格。"""
    return order.channel_reversed_at is not None and order.channel_reversal_action != "release"


class InvoiceRequest(Base):
    """发票申请单。按北京自然月合并开具;amount 由服务端按账期计算。
    部分唯一索引 uq_invoice_requests_active_period:同一 (user_id, period) 只允许一条
    非 rejected 申请。
    """

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
    period: Mapped[str] = mapped_column(String(7))  # 申请账期 YYYY-MM(北京月界)
    title_type: Mapped[str] = mapped_column(String(16))  # personal / company
    title: Mapped[str] = mapped_column(String(128))  # 发票抬头
    tax_id: Mapped[str | None] = mapped_column(String(32))  # 税号(企业抬头必填,个人为空)
    email: Mapped[str] = mapped_column(String(128))  # 接收邮箱
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(16), default="submitted", index=True)
    # submitted → issued / rejected
    invoice_no: Mapped[str | None] = mapped_column(String(64))  # 发票号(开票时填)
    reject_reason: Mapped[str | None] = mapped_column(String(256))
    issued_by: Mapped[int | None]  # 开票操作人(admin_users.id)
    issued_at: Mapped[datetime | None]
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    # 请求体指纹 sha256(user_id|period|抬头三要素|email):同键异参重放 409
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class RefundRequest(Base):
    """退款申请单。审批通过 ≠ 出金:登记打款成功才同事务钱包负向调账,回写 wallet_entry_id。

    DB CHECK payout_by <> review_by。部分唯一索引 uq_refund_requests_active_order:同一订单只允许一条
    pending/approved 申请;同单可多次部分退款,累计不超过订单额。
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
    # R+yyyymmdd+两位日内序列(如 R20260823-01)
    refund_no: Mapped[str] = mapped_column(String(20), unique=True)
    user_id: Mapped[int] = mapped_column(index=True)
    order_no: Mapped[str] = mapped_column(String(40))  # 原充值订单号
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    reason: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # pending → approved / rejected → paid / cancelled
    review_by: Mapped[int | None]  # 审批人(admin_users.id)
    review_at: Mapped[datetime | None]
    review_comment: Mapped[str | None] = mapped_column(String(256))
    payout_channel: Mapped[str | None] = mapped_column(String(32))
    # offline / alipay_transfer / wechat_transfer(线下打款)
    payout_ref: Mapped[str | None] = mapped_column(String(128))  # 线下打款凭证号
    payout_by: Mapped[int | None]  # 打款登记人,强制 ≠ review_by
    payout_at: Mapped[datetime | None]
    wallet_entry_id: Mapped[int | None] = mapped_column(BigInteger)  # 核销后的 balance_ledger.id
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    # 请求体指纹 sha256(user_id|order_no|amount|reason):同键异参重放 409
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    # 打款登记的幂等键,与申请键分开。同键同参且已 paid 回 200 + X-Idempotent-Replay;同键异参 409
    payout_idempotency_key: Mapped[str | None] = mapped_column(String(64))
    payout_request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Subscription(Base):
    """包周期订单:一次性预扣的实例使用权凭证。与 `bills_hourly` 分开,包周期只在候选查询里被跳过
    (见 orchestrator/queries.billing_candidates)。

    续费链:每次续费新开一行,renewed_from_id 指向上一行,老行转 expired。
    """

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
        Index(  # 巡检取「到期在即 / 已到期」的活跃订阅
            "ix_subscriptions_active_expiry",
            "expires_at",
            postgresql_where=text("status = 'active'"),
        ),
        # DB 兜底:一实例仅一行 active(应用层由钱包锁 + 续费行锁串行化)
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
    period: Mapped[str] = mapped_column(String(8))  # day / week / month / year
    period_count: Mapped[int] = mapped_column(default=1)
    # 下单时的 SKU 原价时价快照(未打折),续费按它重新报价;折后时价在 instances.price_hourly
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(14, 2))  # 实扣(已含折扣)
    started_at: Mapped[datetime]
    expires_at: Mapped[datetime] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    # 到期自动续费,默认关
    auto_renew: Mapped[bool] = mapped_column(default=False, server_default="false")
    renewed_from_id: Mapped[int | None]  # 续费链上一环
    # 到期预警去重锚点:最近一次已发预警对应的到期时刻
    warned_for_expiry: Mapped[datetime | None]
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    # 同键重放须过 request_fingerprint 比对,不一致 409;
    # 转换与续费共用 UNIQUE(user_id, idempotency_key)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
