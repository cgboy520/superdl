from datetime import datetime

from sqlalchemy import BigInteger, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Notification(Base):
    """站内信。user_id 为空 = 平台级告警(管理端告警流)。"""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(index=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    # account / instance / balance_warn / arrears / gpu_fault / announcement / admin_alert
    # / recharge / consume / adjust
    title: Mapped[str] = mapped_column(String(128))
    content: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(16), default="info")  # info/warning/critical
    dedup_key: Mapped[str | None] = mapped_column(String(128), unique=True)  # 幂等去重
    read_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
