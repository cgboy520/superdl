from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Wallet(Base):
    """余额。更新必须 SELECT FOR UPDATE + 同事务写 ledger。"""

    __tablename__ = "wallets"
    __table_args__ = (CheckConstraint("frozen_amount >= 0", name="frozen_nonneg"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(unique=True)
    balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    # 恒为 0.00:development-plan 的表设计留了这一列,但同文档 §5「创建时不做预占,
    # 以 K8s 调度结果为准」——MVP 没有任何路径写它。留列不留幻觉:要做预占再启用
    frozen_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class BalanceLedger(Base):
    """追加式资金流水,对账基准。amount 带符号;balance_after 为扣/入账后的快照。"""

    __tablename__ = "balance_ledger"
    # 金额为 0 的流水行没有任何业务含义,只会污染对账口径 —— 出现即代码有 bug,
    # 让它在写入那一刻响亮地失败,而不是变成一行谁也解释不了的记录。
    # 刻意**不**加 wallets.balance >= 0:透支是设计内的(服务已消费完才结算),
    # 加了会让合法的结算扣款整批失败。
    __table_args__ = (CheckConstraint("amount <> 0", name="amount_nonzero"),)

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
    __table_args__ = (
        UniqueConstraint("instance_id", "hour_start"),
        # 单个自然小时窗口最多 3600 秒;越界即窗口计算有 bug(bill_amount 已在应用层拦,
        # 这里是兜底:手工 SQL / 未来的新写入路径不会绕过)
        CheckConstraint("seconds_used >= 0 AND seconds_used <= 3600", name="seconds_range"),
        CheckConstraint("amount >= 0", name="amount_nonneg"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    instance_id: Mapped[int] = mapped_column(index=True)
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
    disk_id: Mapped[int] = mapped_column(index=True)
    user_id: Mapped[int] = mapped_column(index=True)
    day: Mapped[datetime]
    size_gb: Mapped[int]
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 4))  # 元/GB·月
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class SettlementWatermark(Base):
    """结算水位线:已结清的最后一个窗口起点(key='hourly' 存小时,'daily_disk' 存自然日)。

    结算是「从水位线追平到当前」而非只结上一个窗口:worker 停机跨整点/跨日不再永久漏账。
    """

    __tablename__ = "settlement_watermarks"

    key: Mapped[str] = mapped_column(String(16), primary_key=True)
    settled_through: Mapped[datetime]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class Order(Base):
    """充值订单。支付回调幂等靠 channel_txn_id 唯一 + status 检查。"""

    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        CheckConstraint("amount > 0", name="amount_positive"),
    )

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
