from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Instance(Base):
    __tablename__ = "instances"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        # 状态枚举兜底(手工 SQL 旁路防护);合法迁移见 statemachine.TRANSITIONS
        # (naming convention 自动补 ck_<表>_ 前缀,声明短名)
        CheckConstraint(
            "status IN ('creating', 'running', 'stopping', 'stopped', 'starting', 'frozen',"
            " 'releasing', 'released', 'failed')",
            name="status",
        ),
    )

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
    node_name: Mapped[str | None] = mapped_column(String(253))  # 与 node_specs 同宽(K8s 上限 253)
    ssh_port: Mapped[int | None]
    # AES-GCM 密文(enc:v1: 前缀,约 90 字符);存量明文行原样识别,重启/重置后自然轮换
    jupyter_token: Mapped[str] = mapped_column(String(160))
    authorized_keys: Mapped[list[str]] = mapped_column(JSONB, default=list)
    data_disk_id: Mapped[int | None]
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    frozen_deadline: Mapped[datetime | None]  # 冻结回收倒计时(72h)
    # running 实例 Pod 首次 not-ready 的时刻;持续超过宽限即判节点失联(见 reconciler)。
    # 节点失联时 Pod 停在 phase=Running 而 Ready 转 False,只能看这个字段。
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
    """SSH 端口池。instance_id 为空即空闲;blocked=True 表示该端口被集群其它对象占用。

    端口池 30000–32767 与 K8s NodePort 同段,被占端口须标 blocked 让分配器跳过。
    """

    __tablename__ = "port_allocations"
    # 一台实例至多占一个端口(部分唯一:空闲行 instance_id 为 NULL,不参与约束)
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
    """数据盘:独立于实例生命周期(留存抓手)。JuiceFS 子路径,挂载点 /root/data。"""

    __tablename__ = "data_disks"
    # 幂等键:响应丢失后重试不会开出第二块盘;
    # 部分唯一索引:一台实例至多挂一块盘(与 instances.data_disk_id 的 1:1 模型一致)
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
    juicefs_subpath: Mapped[str] = mapped_column(String(128), unique=True)
    price_gb_month: Mapped[Decimal] = mapped_column(Numeric(12, 4))  # 创建时快照
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    # active / grace(欠费宽限,只读) / frozen / deleting / deleted
    mounted_instance_id: Mapped[int | None] = mapped_column(index=True)
    grace_started_at: Mapped[datetime | None]
    frozen_started_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
