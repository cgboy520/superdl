from datetime import datetime
from typing import Any

from sqlalchemy import String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class NodeEnrollment(Base):
    """GPU 服务器注册令牌与加入进度。

    一节点一令牌;token 明文只在创建/重生成响应出现一次,库中仅存 sha256。
    状态机:pending → installing → rebooting ⇆ installing → joining → joined,
    旁路终态 failed / expired / revoked;迁移集中在 service.transition_enrollment。
    """

    __tablename__ = "node_enrollments"
    __table_args__ = (UniqueConstraint("created_by", "idempotency_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256 hex
    pool: Mapped[str] = mapped_column(String(8))  # kata / hami / mig(分池铁律)
    hostname: Mapped[str | None] = mapped_column(String(253))  # 期望主机名(可选,防令牌串用)
    note: Mapped[str | None] = mapped_column(String(128))
    nvme_devices: Mapped[list[str] | None] = mapped_column(JSONB)  # TopoLVM VG 设备(可选)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    phase: Mapped[str | None] = mapped_column(String(32))  # 脚本细粒度进度
    error: Mapped[str | None] = mapped_column(Text)
    node_name: Mapped[str | None] = mapped_column(String(253), index=True)  # bootstrap 上报
    reported_ip: Mapped[str | None] = mapped_column(String(64))
    os_info: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    gpu_info: Mapped[list[str] | None] = mapped_column(JSONB)  # precheck 上报的 GPU 摘要
    expires_at: Mapped[datetime]
    last_report_at: Mapped[datetime | None]  # 心跳:对账器判失联
    joined_at: Mapped[datetime | None]
    created_by: Mapped[int]  # AdminUser.id(仅追溯,不建外键,同 platform_settings 惯例)
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
