from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Index, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Sku(Base):
    """Product spec; changes affect new instances only (snapshotted when the instance is stored)."""

    __tablename__ = "skus"
    __table_args__ = (
        Index(
            "uq_skus_business_key",
            "gpu_model",
            "tier",
            "pool_label",
            "mig_profile",
            "gpu_cores_pct",
            "vcpu",
            "mem_gb",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    gpu_model: Mapped[str] = mapped_column(String(32), index=True)
    tier: Mapped[str] = mapped_column(String(16), index=True)
    mig_profile: Mapped[str | None] = mapped_column(String(32))
    gpu_cores_pct: Mapped[int] = mapped_column(default=100)
    vram_gb: Mapped[int]
    oversell_cores: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=Decimal("1.00"))
    pool_label: Mapped[str] = mapped_column(String(32))
    vcpu: Mapped[int]
    mem_gb: Mapped[int]
    disk_gb: Mapped[int] = mapped_column(default=100)
    price_hourly: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    max_gpus_per_instance: Mapped[int] = mapped_column(default=1)
    cuda_max: Mapped[str | None] = mapped_column(String(16))
    period_enabled: Mapped[bool] = mapped_column(default=True, server_default="true")
    spot_enabled: Mapped[bool] = mapped_column(default=False, server_default="false")
    status: Mapped[str] = mapped_column(String(8), default="off", index=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class PlatformImage(Base):
    """Platform image catalog: data source of the framework → version → Python → CUDA cascade."""

    __tablename__ = "images"

    id: Mapped[int] = mapped_column(primary_key=True)
    framework: Mapped[str] = mapped_column(String(32))
    framework_version: Mapped[str] = mapped_column(String(32))
    python_version: Mapped[str] = mapped_column(String(16))
    cuda_version: Mapped[str] = mapped_column(String(16))
    image_ref: Mapped[str] = mapped_column(String(256), unique=True)
    prewarm_enabled: Mapped[bool] = mapped_column(default=True)
    sort: Mapped[int] = mapped_column(default=0)


class ImageNodeCache(Base):
    """Cached reference, status and last check time per image and node."""

    __tablename__ = "image_node_cache"
    __table_args__ = (UniqueConstraint("image_id", "node_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    image_id: Mapped[int] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"), index=True)
    node_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    cached_ref: Mapped[str | None] = mapped_column(String(256))
    last_error: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
