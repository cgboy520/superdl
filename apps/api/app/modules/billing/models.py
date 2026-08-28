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
    """余额。更新必须 SELECT FOR UPDATE + 同事务写 ledger。"""

    __tablename__ = "wallets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(unique=True)
    balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class BalanceLedger(Base):
    """追加式资金流水,对账基准。amount 带符号;balance_after 为扣/入账后的快照。"""

    __tablename__ = "balance_ledger"
    # amount <> 0:零额流水无业务含义。刻意不加 balance >= 0:透支是设计内的(先消费后结算)
    __table_args__ = (CheckConstraint("amount <> 0", name="amount_nonzero"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    type: Mapped[str] = mapped_column(String(16))  # recharge / consume / refund / adjust
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    balance_after: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    ref_type: Mapped[str | None] = mapped_column(String(32))  # bill_hourly / order / adjustment...
    ref_id: Mapped[str | None] = mapped_column(String(64))
    remark: Mapped[str | None] = mapped_column(String(256))
    # 翻页/对账一律走主键 id,created_at 无查询使用,不建索引
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class BillHourly(Base):
    """实例小时账单。UNIQUE(instance_id, hour_start) 即结算幂等键。"""

    __tablename__ = "bills_hourly"
    __table_args__ = (
        UniqueConstraint("instance_id", "hour_start"),
        # 单个自然小时窗口最多 3600 秒;应用层已拦,这里兜住手工 SQL 等旁路写入
        CheckConstraint("seconds_used >= 0 AND seconds_used <= 3600", name="seconds_range"),
        CheckConstraint("amount >= 0", name="amount_nonneg"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # 查询走 UniqueConstraint(instance_id, hour_start) 前导列,不建冗余单列索引
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
    """数据盘日结。UNIQUE(disk_id, day) 幂等;关机也扣(「日常费用」)。"""

    __tablename__ = "bills_daily_disk"
    __table_args__ = (
        UniqueConstraint("disk_id", "day"),
        CheckConstraint("amount >= 0", name="amount_nonneg"),
        CheckConstraint("size_gb >= 0", name="size_nonneg"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    disk_id: Mapped[int]  # 查询走 UniqueConstraint(disk_id, day) 前导列,不建冗余单列索引
    user_id: Mapped[int] = mapped_column(index=True)
    day: Mapped[datetime]
    size_gb: Mapped[int]
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 4))  # 元/GB·月
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class SettlementWatermark(Base):
    """结算水位线:已结清的最后一个窗口起点(key='hourly' 存小时,'daily_disk' 存自然日)。

    结算口径是「从水位线追平到当前」而非只结上一个窗口,worker 停机跨整点/跨日可自动补上。
    """

    __tablename__ = "settlement_watermarks"

    key: Mapped[str] = mapped_column(String(16), primary_key=True)
    settled_through: Mapped[datetime]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class SettlementGap(Base):
    """结算缺口登记:水位线越过但账未结清的窗口,一律在此留痕。

    两个来源:追平截断(catchup_truncated,整窗跳过,object_id=0)与死信
    (dead_letter,单对象连续失败超限)。只登记不自动补:由补结任务或人工按
    (kind, window_start, object_id) 追溯,处理后标记 resolved_at。
    """

    __tablename__ = "settlement_gaps"
    __table_args__ = (UniqueConstraint("kind", "window_start", "object_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # hourly / daily_disk
    window_start: Mapped[datetime]  # 缺口窗口起点(小时/自然日)
    object_id: Mapped[int] = mapped_column(BigInteger, default=0)  # 实例/盘 id;0 = 整窗截断
    reason: Mapped[str] = mapped_column(String(32))  # catchup_truncated / dead_letter
    resolved_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ReconcileCheckpoint(Base):
    """钱包-流水链式核对的增量游标:该用户已验到的最后一笔流水。

    balance_after 为该笔提交后的钱包快照;updated_at 为本轮扫描开始的库时钟,
    与 wallets.updated_at 比较决定下轮是否需重验(只扫增量流水)。
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
        # 人工补单幂等键 DB 兜底:同键只可能落在一笔订单上(应用层先判重放,约束兜并发)
        UniqueConstraint("backfill_idempotency_key"),
        CheckConstraint("amount > 0", name="amount_positive"),
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
    # 管理端人工补单的幂等键:同键重放直接回当前状态,不报「已入账」409
    backfill_idempotency_key: Mapped[str | None] = mapped_column(String(64))
    qr_url: Mapped[str | None] = mapped_column(String(512))
    paid_at: Mapped[datetime | None]
    # 渠道侧对已入账订单的关单/退款通知到达时刻(不自动冲账,人工核销;异常清单分桶依据)
    channel_reversed_at: Mapped[datetime | None]
    expires_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class InvoiceRequest(Base):
    """发票申请单。按账期合并开具:一个自然月一张;amount 由服务端按账期计算
    (Σ 该账期 paid 充值 − Σ 该账期 submitted+issued 申请),客户端只提交账期与抬头。

    部分唯一索引 uq_invoice_requests_active_period:同一 (user_id, period) 只允许一条
    非 rejected 申请(rejected 不占位,用户可修改抬头后重新申请)。
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
    email: Mapped[str] = mapped_column(String(128))  # 接收邮箱(人工开票后发送至该邮箱)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(16), default="submitted", index=True)
    # submitted → issued / rejected
    invoice_no: Mapped[str | None] = mapped_column(String(64))  # 发票号(开票时填)
    reject_reason: Mapped[str | None] = mapped_column(String(256))
    issued_by: Mapped[int | None]  # 开票操作人(admin_users.id,finance/admin)
    issued_at: Mapped[datetime | None]
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class RefundRequest(Base):
    """退款申请单。审批通过 ≠ 出金:登记打款成功才同事务钱包负向调账,
    并回写 wallet_entry_id 关联 balance_ledger。

    双人制衡硬约束:DB CHECK 兜底 payout_by <> review_by(应用层同样拦截给 409 文案)。
    部分唯一索引 uq_refund_requests_active_order:同一订单只允许一条活跃申请
    (rejected/cancelled 后用户可重新申请)。
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
            postgresql_where=text("status IN ('pending', 'approved', 'paid')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # R+yyyymmdd+两位日内序列(如 R20260823-01);序列由服务层当日计数+唯一冲突重试生成
    refund_no: Mapped[str] = mapped_column(String(20), unique=True)
    user_id: Mapped[int] = mapped_column(index=True)
    order_no: Mapped[str] = mapped_column(String(40))  # 原充值订单号
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    reason: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # pending → approved / rejected → paid / cancelled
    review_by: Mapped[int | None]  # 审批人(admin_users.id,finance/admin)
    review_at: Mapped[datetime | None]
    review_comment: Mapped[str | None] = mapped_column(String(256))
    payout_channel: Mapped[str | None] = mapped_column(String(32))
    # offline / alipay_transfer / wechat_transfer(线下打款;不做渠道原路退回)
    payout_ref: Mapped[str | None] = mapped_column(String(128))  # 线下打款凭证号
    payout_by: Mapped[int | None]  # 打款登记人,强制 ≠ review_by
    payout_at: Mapped[datetime | None]
    wallet_entry_id: Mapped[int | None] = mapped_column(BigInteger)  # 核销后的 balance_ledger.id
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Subscription(Base):
    """包周期订单:一次性预扣的「实例使用权」凭证。

    与小时账单分开:`bills_hourly` 的结构、幂等键、水位线、缺口机制不涉及包周期,
    包周期只在候选查询里被跳过(见 orchestrator/queries.billing_candidates)。

    续费链:每次续费**新开一行**并把 renewed_from_id 指向上一行,老行转 expired。
    不在原行上累加 expires_at:账期归属(哪笔钱属于哪个月的收入)要看得见,
    跨月续费时老周期与新周期的金额分别落在各自的行上。
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
        Index(  # 巡检取「到期在即 / 已到期」的活跃订阅,一条索引服务两种谓词
            "ix_subscriptions_active_expiry",
            "expires_at",
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    instance_id: Mapped[int] = mapped_column(index=True)
    sku_id: Mapped[int]
    period: Mapped[str] = mapped_column(String(8))  # day / week / month / year
    period_count: Mapped[int] = mapped_column(default=1)
    # 下单时的 SKU 原价时价快照(未打折),续费按它重新报价:SKU 涨价不追已购用户。
    # 折后时价在 instances.price_hourly 上(见 core/pricing.price_for)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(14, 2))  # 实扣(已含折扣)
    started_at: Mapped[datetime]
    expires_at: Mapped[datetime] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    # 到期自动续费。默认关:可无限重复的扣款授权必须由用户主动开启
    auto_renew: Mapped[bool] = mapped_column(default=False, server_default="false")
    renewed_from_id: Mapped[int | None]  # 续费链上一环,便于账期追溯
    # 到期预警的去重锚点。存「最近一次已发预警对应的到期时刻」而不是布尔:续费后
    # expires_at 变了,新周期的预警自然重新可发,不需要额外清位
    warned_for_expiry: Mapped[datetime | None]
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
