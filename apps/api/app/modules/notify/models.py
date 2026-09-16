from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Notification(Base):
    """In-app notification. user_id empty = platform-level alert (admin alert feed)."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(index=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(128))
    content: Mapped[str] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(String(64))
    target_kind: Mapped[str | None] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(16), default="info")
    status: Mapped[str] = mapped_column(String(16), default="published", server_default="published")
    dedup_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    read_at: Mapped[datetime | None]
    acked_by: Mapped[int | None]
    acked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Announcement(Base):
    """Announcement publication, reach count and withdrawal record; the publish idempotency key is
    globally unique."""

    __tablename__ = "announcements"
    __table_args__ = (CheckConstraint("status IN ('published', 'revoked')", name="status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="published")
    reached: Mapped[int] = mapped_column(default=0)
    created_by: Mapped[int]
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    revoked_by: Mapped[int | None]
    revoked_at: Mapped[datetime | None]
    revoke_reason: Mapped[str | None] = mapped_column(String(256))
