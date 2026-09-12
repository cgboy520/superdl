from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class NodeEnrollment(Base):
    """GPU 服务器注册令牌与加入进度。一节点一令牌,库中只存 HMAC-SHA256。
    状态机:pending → installing → rebooting ⇆ installing → joining → joined,
    旁路终态 failed / expired / revoked;迁移集中在 service.transition_enrollment。
    """

    __tablename__ = "node_enrollments"
    __table_args__ = (UniqueConstraint("created_by", "idempotency_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # HMAC-SHA256 hex
    # bootstrap 换发的窄权限令牌(仅 /progress),只存 HMAC 摘要;NULL = 尚未 bootstrap 或已轮换
    progress_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    pool: Mapped[str] = mapped_column(String(8))  # kata / hami / mig / cpu(分池铁律)
    hostname: Mapped[str | None] = mapped_column(String(253))  # 期望主机名(签发时必填,防令牌串用)
    note: Mapped[str | None] = mapped_column(String(128))
    nvme_devices: Mapped[list[str] | None] = mapped_column(JSONB)  # TopoLVM VG 设备(可选)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    phase: Mapped[str | None] = mapped_column(String(32))  # 脚本细粒度进度
    error: Mapped[str | None] = mapped_column(Text)
    node_name: Mapped[str | None] = mapped_column(String(253))  # bootstrap 上报
    reported_ip: Mapped[str | None] = mapped_column(String(64))
    os_info: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    gpu_info: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB
    )  # 全卡清单 [{name, memory_mib?}]
    expires_at: Mapped[datetime]
    last_report_at: Mapped[datetime | None]  # 心跳:对账器判失联
    joined_at: Mapped[datetime | None]
    created_by: Mapped[int]  # AdminUser.id(仅追溯,不建外键,同 platform_settings 惯例)
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class NodeSpec(Base):
    """节点规格台账:巡检(nodes/patrol.py,60s)从 K8s 实况 + 装机登记收敛;业务只读它。
    节点消失先置 Missing,last_seen 超 7 天删行;上架校验只认 Ready;未打池标签节点 unlabeled=True。
    """

    __tablename__ = "node_specs"

    id: Mapped[int] = mapped_column(primary_key=True)
    node_name: Mapped[str] = mapped_column(String(253), unique=True)
    pool_label: Mapped[str | None] = mapped_column(String(32))
    unlabeled: Mapped[bool] = mapped_column(default=False)  # 无 superdl.io/pool 标签
    gpu_model_raw: Mapped[str | None] = mapped_column(String(128))  # nvidia-smi/GFD 原文
    gpu_model: Mapped[str | None] = mapped_column(String(32))  # canonical;None=未识别
    label_synced: Mapped[bool] = mapped_column(default=False)  # superdl.io/gpu-model 已收敛
    gpu_count: Mapped[int] = mapped_column(default=0)
    gpu_used: Mapped[int] = mapped_column(default=0)  # 展示用,60s 粒度
    vram_gb: Mapped[int] = mapped_column(default=0)  # 单卡显存;0=未知
    vcpu: Mapped[int] = mapped_column(default=0)
    mem_gb: Mapped[int] = mapped_column(default=0)
    disk_gb: Mapped[int] = mapped_column(default=0)
    # 来自装机登记快照 os_info;未经本平台装机则为空
    driver_version: Mapped[str | None] = mapped_column(String(32))
    cuda_version: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), index=True)  # Ready/NotReady/Cordoned/Missing
    # cordon 期望态:管理端写入,handler/巡检按它收敛
    desired_unschedulable: Mapped[bool | None]
    last_seen: Mapped[datetime]  # 最近一次 K8s 可见
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class ClusterStatus(Base):
    """集群能力缓存(单行 id=1):巡检探测落库,门禁与集群页只读;probed_at 超 10min 视为未知。"""

    __tablename__ = "cluster_status"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(primary_key=True)  # 恒为 1
    api_reachable: Mapped[bool] = mapped_column(default=False)
    k8s_version: Mapped[str | None] = mapped_column(String(64))
    distro: Mapped[str | None] = mapped_column(String(16))  # rke2 / k3s / None=未知
    hami_ready: Mapped[bool] = mapped_column(default=False)
    dcgm_present: Mapped[bool] = mapped_column(default=False)
    kps_present: Mapped[bool] = mapped_column(default=False)
    gpu_operator_present: Mapped[bool] = mapped_column(default=False)
    kata_runtimeclass: Mapped[bool] = mapped_column(default=False)
    nvidia_runtimeclass: Mapped[bool] = mapped_column(default=False, server_default="false")
    # Gateway 对象 status Programmed=True
    gateway_ready: Mapped[bool] = mapped_column(default=False, server_default="false")
    cert_manager_ready: Mapped[bool] = mapped_column(default=False, server_default="false")
    nodes_ready: Mapped[int] = mapped_column(default=0, server_default="0")
    nodes_total: Mapped[int] = mapped_column(default=0, server_default="0")
    storage_classes: Mapped[list[str] | None] = mapped_column(JSONB)
    pools: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # 池→节点数
    error: Mapped[str | None] = mapped_column(Text)
    probed_at: Mapped[datetime]
