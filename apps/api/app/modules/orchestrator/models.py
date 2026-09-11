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
        # 状态枚举兜底;合法迁移见 statemachine.TRANSITIONS。约束名由 naming convention 补前缀
        CheckConstraint(
            "status IN ('creating', 'running', 'stopping', 'stopped', 'starting', 'frozen',"
            " 'releasing', 'released', 'failed')",
            name="status",
        ),
        # CPU 实例 gpu_count=0 合法
        CheckConstraint("gpu_count >= 0", name="gpu_count_nonneg"),
        # 形态枚举兜底:dev = SSH + JupyterLab 开发机,service = 在线服务的一个版本
        CheckConstraint("workload_type IN ('dev', 'service')", name="workload_type"),
        # 形态与服务归属同真同假
        CheckConstraint(
            "(workload_type = 'service') = (service_id IS NOT NULL)", name="service_shape"
        ),
        # 22 / 8888 为平台占用端口(sshd / JupyterLab)
        CheckConstraint(
            "service_port IS NULL OR (service_port BETWEEN 1 AND 65535"
            " AND service_port NOT IN (22, 8888))",
            name="service_port",
        ),
        # 购买模式枚举兜底
        CheckConstraint(
            "market IN ('on_demand', 'spot', 'subscription')",
            name="market",
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
    # 购买模式:on_demand / subscription(已预付)/ spot;与 skus.tier 正交
    market: Mapped[str] = mapped_column(
        String(16), default=MARKET_ON_DEMAND, server_default=MARKET_ON_DEMAND
    )
    # 实例形态:dev(SSH + JupyterLab)/ service(在线服务的一个版本);只决定 Pod 形态
    workload_type: Mapped[str] = mapped_column(String(8), default="dev", server_default="dev")
    # 归属的在线服务(services.id)与版本号;dev 恒空
    service_id: Mapped[int | None] = mapped_column(index=True)
    service_revision: Mapped[int | None]
    # 服务暴露规格快照(随版本走);建 Pod 只读这几列,不查 services 表
    service_slug: Mapped[str | None] = mapped_column(String(32))
    service_port: Mapped[int | None]
    # 非空 → readinessProbe + startupProbe;空 = 容器起来即就绪
    health_path: Mapped[str | None] = mapped_column(String(128))
    # 覆盖镜像 ENTRYPOINT/CMD;None = 镜像自带(dev 恒 None)
    container_command: Mapped[list[str] | None] = mapped_column(JSONB)
    container_args: Mapped[list[str] | None] = mapped_column(JSONB)
    # 是否开 SSH:dev 恒 True,service 默认 False;不从 ssh_port 反推
    with_ssh: Mapped[bool] = mapped_column(default=True, server_default="true")
    # 用户环境变量整包密文(AES-GCM,AAD 绑实例 uuid),明文形如 {"plain": {...}, "secret": {...}}
    env_encrypted: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), index=True)
    version: Mapped[int] = mapped_column(default=0)  # 乐观锁
    k8s_namespace: Mapped[str] = mapped_column(String(64))
    node_name: Mapped[str | None] = mapped_column(String(253))  # 与 node_specs 同宽(K8s 上限 253)
    ssh_port: Mapped[int | None]
    # AES-GCM 密文(enc:v2:<kid>: 前缀,约 103 字符),AAD 绑实例 uuid
    jupyter_token: Mapped[str] = mapped_column(String(160))
    authorized_keys: Mapped[list[str]] = mapped_column(JSONB, default=list)
    data_disk_id: Mapped[int | None]
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    # 请求体指纹 sha256(见 service.instance_fingerprint):同键异参 409
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    # 冻结回收截止:进入 frozen 时按 freeze_grace_hours 写入(billing/patrol.py)
    frozen_deadline: Mapped[datetime | None]
    # running 实例 Pod 首次 not-ready 的时刻;超宽限判失联(reconciler)
    unready_since: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class InstanceEvent(Base):
    """状态迁移流水:计费主依据 + 用户时间线,追加式。"""

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
    """SSH 端口池(30000–32767):instance_id 空即空闲;blocked = 被集群其它对象占用,分配器跳过。"""

    __tablename__ = "port_allocations"
    # 一台实例至多占一个端口(部分唯一)
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
    """数据盘:独立于实例生命周期;JuiceFS 子路径,挂载点 /root/data。"""

    __tablename__ = "data_disks"
    # 一台实例至多挂一块盘(部分唯一)
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
    # 请求体指纹 sha256(user_id|name|size_gb):同键异参 409
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    # active / grace(欠费宽限,只读) / frozen / deleting / deleted
    mounted_instance_id: Mapped[int | None] = mapped_column(index=True)
    grace_started_at: Mapped[datetime | None]
    # 最近一次离开 grace/frozen 的时刻;日结按 [grace_started, grace_ended) 判宽限日
    grace_ended_at: Mapped[datetime | None]
    frozen_started_at: Mapped[datetime | None]
    # JuiceFS 目录配额是否已按 size_gb 下发(创建/扩容置 false 并入队 disk.quota);false 时不可挂载
    quota_synced: Mapped[bool] = mapped_column(default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
