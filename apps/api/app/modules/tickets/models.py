from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Ticket(Base):
    """工单。状态机:open → pending_staff(用户回复)→ pending_user(客服回复)
    → resolved(任一方标记解决)→ closed(仅 resolved 后可关);resolved/closed 不可再回复。

    (user_id, idempotency_key) 唯一:创建幂等,重放返回既有单(NULL 互不冲突)。
    """

    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        CheckConstraint(
            "category IN ('instance', 'billing', 'data', 'account', 'other')", name="category"
        ),
        CheckConstraint(
            "status IN ('open', 'pending_staff', 'pending_user', 'resolved', 'closed')",
            name="status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # T+yyyymmdd+两位日内序列(如 T20260823-01);序列由服务层当日计数+唯一冲突重试生成
    ticket_no: Mapped[str] = mapped_column(String(20), unique=True)
    user_id: Mapped[int] = mapped_column(index=True)
    category: Mapped[str] = mapped_column(String(16))
    subject: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    instance_uuid: Mapped[str | None] = mapped_column(String(32))  # 关联实例(可选,快照)
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    closed_at: Mapped[datetime | None]  # 仅 closed 落


class TicketMessage(Base):
    """工单消息(对话流)。sender_kind=user/staff;resolved/closed 工单由服务层拒绝追加。"""

    __tablename__ = "ticket_messages"
    __table_args__ = (CheckConstraint("sender_kind IN ('user', 'staff')", name="sender_kind"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    sender_kind: Mapped[str] = mapped_column(String(8))  # user / staff
    sender_id: Mapped[int]  # users.id 或 admin_users.id(按 sender_kind 解读,不建外键)
    body: Mapped[str] = mapped_column(String(4000))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
