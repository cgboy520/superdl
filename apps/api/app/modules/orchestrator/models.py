from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Instance(Base):
    __tablename__ = "instances"
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column(String(32), unique=True)  # k8s 对象名
    user_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(64))
    sku_id: Mapped[int]
    # SKU 快照:变更 SKU 仅影响新实例
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    price_hourly: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    gpu_count: Mapped[int] = mapped_column(default=1)
    image_ref: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), index=True)
    version: Mapped[int] = mapped_column(default=0)  # 乐观锁
    k8s_namespace: Mapped[str] = mapped_column(String(64))
    pod_name: Mapped[str | None] = mapped_column(String(64))
    node_name: Mapped[str | None] = mapped_column(String(64))
    ssh_port: Mapped[int | None]
    jupyter_token: Mapped[str] = mapped_column(String(64))
    authorized_keys: Mapped[list[str]] = mapped_column(JSONB, default=list)
    data_disk_id: Mapped[int | None]
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    frozen_deadline: Mapped[datetime | None]  # 冻结回收倒计时(72h)
    # running 实例 Pod 首次 not-ready 的时刻。节点失联时 Pod 停在 phase=Running 而 Ready 转
    # False,判失联必须看这个字段;持续 not-ready 超过宽限即判失联(见 reconciler)。
    unready_since: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class InstanceEvent(Base):
    """状态迁移流水:计费主依据 + 用户可见时间线。追加式不可改。"""

    __tablename__ = "instance_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    instance_id: Mapped[int] = mapped_column(index=True)
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(16))  # user / system / admin
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)


class PortAllocation(Base):
    """SSH 端口池。instance_id 为空即空闲;blocked=True 表示该端口被集群里的其它对象占用。

    端口池 30000–32767 与 K8s NodePort 同段,集群内其它对象会硬占其中某些端口。
    没有 blocked 这一列,分配器在「最小空闲」耗尽后一路取 max+1,每次都算出同一个被占端口、
    每次 Service 创建 422、事务整体回滚,此后所有触顶的新建实例全部失败。
    """

    __tablename__ = "port_allocations"

    id: Mapped[int] = mapped_column(primary_key=True)
    port: Mapped[int] = mapped_column(unique=True)
    instance_id: Mapped[int | None] = mapped_column(index=True)
    blocked: Mapped[bool] = mapped_column(default=False, server_default="false")
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class DataDisk(Base):
    """数据盘:独立于实例生命周期(留存抓手)。JuiceFS 子路径,挂载点 /root/data。"""

    __tablename__ = "data_disks"
    # 与实例创建同款:响应丢失时重试不会开出第二块按日计费的盘
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column(String(32), unique=True)
    user_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(64))
    size_gb: Mapped[int]
    juicefs_subpath: Mapped[str] = mapped_column(String(128), unique=True)
    price_gb_month: Mapped[Decimal] = mapped_column(Numeric(12, 4))  # 创建时快照
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    # active / grace(欠费宽限,只读) / frozen / deleting / deleted
    mounted_instance_id: Mapped[int | None] = mapped_column(index=True)
    grace_started_at: Mapped[datetime | None]
    frozen_started_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
