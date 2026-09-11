from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AdminUser(Base):
    """管理端账号,与租户体系隔离(独立登录与 JWT audience)。"""

    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16))  # admin / ops / finance / readonly
    status: Mapped[str] = mapped_column(String(16), default="active")
    # 撤销闸:停用、改角色、改密都 +1
    token_version: Mapped[int] = mapped_column(default=0, server_default="0")
    # TOTP(全部管理角色强制):secret AES-GCM 加密(aad=f"totp:{id}");recovery 为恢复码 bcrypt 哈希列表
    totp_secret: Mapped[str | None] = mapped_column(String(255))
    totp_enabled: Mapped[bool] = mapped_column(default=False, server_default="false")
    totp_recovery: Mapped[list[str] | None] = mapped_column(JSONB)
    # TOTP 防重放:已通过验证的最大 timestep(30s),行锁内单调推进;≤ 此步的码拒绝。NULL = 从未验证
    last_totp_timestep: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class AdminAdjustment(Base):
    """调账单:发起 → 第二管理员复核 → 生效。"""

    __tablename__ = "admin_adjustments"
    # 幂等键作用域 (发起人, 租户, 键);同键重放须过 request_fingerprint 比对,不一致 409
    __table_args__ = (
        UniqueConstraint(
            "created_by", "user_id", "idempotency_key", name="uq_admin_adjustments_idem_scope"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))  # 带符号:正=补偿,负=扣减
    reason: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    # pending / approved / rejected
    created_by: Mapped[int]  # admin_users.id
    reviewed_by: Mapped[int | None]
    review_comment: Mapped[str | None] = mapped_column(String(256))
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    # 请求体 SHA256(user_id|amount|reason):同键重放比对用
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    reviewed_at: Mapped[datetime | None]
