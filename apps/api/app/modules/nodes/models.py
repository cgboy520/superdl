from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class NodeEnrollment(Base):
    """节点注册与进度令牌仅存 HMAC-SHA256 摘要。

    状态:pending → installing → rebooting ⇆ installing → joining → joined;
    旁路终态 failed / expired / revoked。
    """

    __tablename__ = "node_enrollments"
    __table_args__ = (UniqueConstraint("created_by", "idempotency_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    progress_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    pool: Mapped[str] = mapped_column(String(8))
    hostname: Mapped[str | None] = mapped_column(String(253))
    note: Mapped[str | None] = mapped_column(String(128))
    nvme_devices: Mapped[list[str] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    phase: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)
    node_name: Mapped[str | None] = mapped_column(String(253))
    reported_ip: Mapped[str | None] = mapped_column(String(64))
    os_info: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    gpu_info: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    expires_at: Mapped[datetime]
    last_report_at: Mapped[datetime | None]
    joined_at: Mapped[datetime | None]
    created_by: Mapped[int]
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class NodeSpec(Base):
    """巡检收敛的节点台账;消失置 Missing,last_seen 超 7 天删行。

    管理端写 desired_unschedulable 与 desired_pool;非空 desired_pool 优先于注册登记。
    """

    __tablename__ = "node_specs"

    id: Mapped[int] = mapped_column(primary_key=True)
    node_name: Mapped[str] = mapped_column(String(253), unique=True)
    pool_label: Mapped[str | None] = mapped_column(String(32))
    unlabeled: Mapped[bool] = mapped_column(default=False)
    gpu_model_raw: Mapped[str | None] = mapped_column(String(128))
    gpu_model: Mapped[str | None] = mapped_column(String(32))
    label_synced: Mapped[bool] = mapped_column(default=False)
    gpu_count: Mapped[int] = mapped_column(default=0)
    gpu_used: Mapped[int] = mapped_column(default=0)
    vram_gb: Mapped[int] = mapped_column(default=0)
    vcpu: Mapped[int] = mapped_column(default=0)
    mem_gb: Mapped[int] = mapped_column(default=0)
    disk_gb: Mapped[int] = mapped_column(default=0)
    driver_version: Mapped[str | None] = mapped_column(String(32))
    cuda_version: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), index=True)
    desired_unschedulable: Mapped[bool | None]
    desired_pool: Mapped[str | None] = mapped_column(String(8))
    last_seen: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class ClusterStatus(Base):
    """集群能力缓存(单行 id=1):巡检探测落库,门禁与集群页只读;probed_at 超 10min 视为未知。"""

    __tablename__ = "cluster_status"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    api_reachable: Mapped[bool] = mapped_column(default=False)
    k8s_version: Mapped[str | None] = mapped_column(String(64))
    distro: Mapped[str | None] = mapped_column(String(16))
    hami_ready: Mapped[bool] = mapped_column(default=False)
    dcgm_present: Mapped[bool] = mapped_column(default=False)
    kps_present: Mapped[bool] = mapped_column(default=False)
    gpu_operator_present: Mapped[bool] = mapped_column(default=False)
    kata_runtimeclass: Mapped[bool] = mapped_column(default=False)
    nvidia_runtimeclass: Mapped[bool] = mapped_column(default=False, server_default="false")
    gateway_ready: Mapped[bool] = mapped_column(default=False, server_default="false")
    cert_manager_ready: Mapped[bool] = mapped_column(default=False, server_default="false")
    nodes_ready: Mapped[int] = mapped_column(default=0, server_default="0")
    nodes_total: Mapped[int] = mapped_column(default=0, server_default="0")
    storage_classes: Mapped[list[str] | None] = mapped_column(JSONB)
    pools: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    pools_ready: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    component_facts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    probed_at: Mapped[datetime]
