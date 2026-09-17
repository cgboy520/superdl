from datetime import datetime

from sqlalchemy import BigInteger, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class UsageHourly(Base):
    """Hourly usage aggregated from Prometheus. Display and reconciliation only, never billing."""

    __tablename__ = "usage_hourly"
    __table_args__ = (UniqueConstraint("instance_id", "hour_start"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    instance_id: Mapped[int] = mapped_column(index=True)
    hour_start: Mapped[datetime] = mapped_column(index=True)
    gpu_util_avg: Mapped[float | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
