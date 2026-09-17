from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.pricing import MARKET_ON_DEMAND


class Instance(Base):
    __tablename__ = "instances"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        Index("ix_instances_user_id_id", "user_id", "id"),
        CheckConstraint(
            "status IN ('creating', 'running', 'stopping', 'stopped', 'starting', 'frozen',"
            " 'releasing', 'released', 'failed')",
            name="status",
        ),
        CheckConstraint("gpu_count >= 0", name="gpu_count_nonneg"),
        CheckConstraint("workload_type IN ('dev', 'service')", name="workload_type"),
        CheckConstraint(
            "(workload_type = 'service') = (service_id IS NOT NULL)", name="service_shape"
        ),
        CheckConstraint(
            "service_port IS NULL OR (service_port BETWEEN 1 AND 65535"
            " AND service_port NOT IN (22, 8888))",
            name="service_port",
        ),
        CheckConstraint(
            "market IN ('on_demand', 'spot', 'subscription')",
            name="market",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column(String(32), unique=True)
    user_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(64))
    sku_id: Mapped[int]
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    price_hourly: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    gpu_count: Mapped[int] = mapped_column(default=1)
    image_ref: Mapped[str] = mapped_column(String(256))
    market: Mapped[str] = mapped_column(
        String(16), default=MARKET_ON_DEMAND, server_default=MARKET_ON_DEMAND
    )
    workload_type: Mapped[str] = mapped_column(String(8), default="dev", server_default="dev")
    service_id: Mapped[int | None] = mapped_column(index=True)
    service_revision: Mapped[int | None]
    service_slug: Mapped[str | None] = mapped_column(String(32))
    service_port: Mapped[int | None]
    health_path: Mapped[str | None] = mapped_column(String(128))
    container_command: Mapped[list[str] | None] = mapped_column(JSONB)
    container_args: Mapped[list[str] | None] = mapped_column(JSONB)
    with_ssh: Mapped[bool] = mapped_column(default=True, server_default="true")
    env_encrypted: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), index=True)
    version: Mapped[int] = mapped_column(default=0)
    k8s_namespace: Mapped[str] = mapped_column(String(64))
    node_name: Mapped[str | None] = mapped_column(String(253))
    ssh_port: Mapped[int | None]
    jupyter_token: Mapped[str] = mapped_column(String(160))
    authorized_keys: Mapped[list[str]] = mapped_column(JSONB, default=list)
    data_disk_id: Mapped[int | None]
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    frozen_deadline: Mapped[datetime | None]
    unready_since: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class InstanceEvent(Base):
    """State-transition ledger: primary billing basis + user timeline, append-only."""

    __tablename__ = "instance_events"
    __table_args__ = (Index("ix_instance_events_instance_id_id", "instance_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    instance_id: Mapped[int] = mapped_column(index=True)
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(16))
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)


class PortAllocation(Base):
    """SSH port pool (30000–32767): instance_id empty = free; blocked = held by another cluster
    object, skipped by the allocator."""

    __tablename__ = "port_allocations"
    __table_args__ = (
        Index(
            "uq_port_allocations_instance",
            "instance_id",
            unique=True,
            postgresql_where=text("instance_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    port: Mapped[int] = mapped_column(unique=True)
    instance_id: Mapped[int | None] = mapped_column(index=True)
    blocked: Mapped[bool] = mapped_column(default=False, server_default="false")
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class DataDisk(Base):
    """Data disk: independent of the instance lifecycle; one CephFS PVC per disk (named
    disk-<uuid>), mount point /root/data."""

    __tablename__ = "data_disks"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        Index(
            "uq_data_disks_mounted_instance",
            "mounted_instance_id",
            unique=True,
            postgresql_where=text("mounted_instance_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column(String(32), unique=True)
    user_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(64))
    size_gb: Mapped[int]
    price_gb_month: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    mounted_instance_id: Mapped[int | None] = mapped_column(index=True)
    grace_started_at: Mapped[datetime | None]
    grace_ended_at: Mapped[datetime | None]
    frozen_started_at: Mapped[datetime | None]
    provisioned: Mapped[bool] = mapped_column(default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
