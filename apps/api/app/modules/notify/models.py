from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Notification(Base):
    """站内信。user_id 为空 = 平台级告警(管理端告警流)。"""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(index=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    # account / adjust / admin_alert / announcement / arrears / balance_warn / consume
    # / gpu_fault / instance / invoice / preempted / recharge / refund / service
    # / subscription / ticket
    title: Mapped[str] = mapped_column(String(128))
    content: Mapped[str] = mapped_column(Text)
    # 结构化跳转目标(通知中心深链):instance 类 = 实例 uuid,ticket 类 = 工单 id;无目标恒空
    target_id: Mapped[str | None] = mapped_column(String(64))
    # 管理端告警流的跳转种类(tenant / node / ticket),写入时给全;用户端通知恒空
    target_kind: Mapped[str | None] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(16), default="info")  # info/warning/critical
    # published / revoked:仅 announcement 类型会被撤回,其余类型恒 published
    status: Mapped[str] = mapped_column(String(16), default="published", server_default="published")
    dedup_key: Mapped[str | None] = mapped_column(String(128), unique=True)  # 幂等去重
    read_at: Mapped[datetime | None]
    # 告警闭环:管理端告警流确认留痕;非告警行恒空
    acked_by: Mapped[int | None]  # admin_users.id
    acked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Announcement(Base):
    """公告记录;用户端触达走 Notification fanout,fanout 行 dedup_key = f"ann:{id}:{user_id}"。
    idempotency_key 为发布接口幂等键。
    """

    __tablename__ = "announcements"
    __table_args__ = (CheckConstraint("status IN ('published', 'revoked')", name="status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="published")
    reached: Mapped[int] = mapped_column(default=0)  # 发布时触达的 active 用户数
    created_by: Mapped[int]  # admin_users.id
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    revoked_by: Mapped[int | None]  # admin_users.id
    revoked_at: Mapped[datetime | None]
    revoke_reason: Mapped[str | None] = mapped_column(String(256))
