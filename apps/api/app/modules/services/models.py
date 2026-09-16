"""Online service identity, gateway attributes and access keys; the service status derives from the
current / candidate instances and released_at."""

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

DESIRED_RUNNING = "running"
DESIRED_STOPPED = "stopped"


class Service(Base):
    __tablename__ = "services"
    __table_args__ = (
        CheckConstraint("protocol IN ('http')", name="protocol"),
        CheckConstraint("desired_state IN ('running', 'stopped')", name="desired_state"),
        Index("ix_services_user_id_id", "user_id", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    public_slug: Mapped[str] = mapped_column(String(32), unique=True)
    user_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(64))
    protocol: Mapped[str] = mapped_column(String(8), default="http", server_default="http")
    require_api_key: Mapped[bool] = mapped_column(default=True, server_default="true")
    desired_state: Mapped[str] = mapped_column(
        String(8), default=DESIRED_RUNNING, server_default=DESIRED_RUNNING
    )
    current_instance_id: Mapped[int | None]
    rollout_instance_id: Mapped[int | None]
    revision: Mapped[int] = mapped_column(default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    released_at: Mapped[datetime | None]


class ServiceApiKey(Base):
    """Service access key; only the HMAC digest and display prefix are stored, the plaintext is
    returned at creation only; revocation keeps the row."""

    __tablename__ = "service_api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    service_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(64))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    key_prefix: Mapped[str] = mapped_column(String(16))
    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
