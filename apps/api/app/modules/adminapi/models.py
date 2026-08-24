from datetime import datetime
from decimal import Decimal

from sqlalchemy import Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AdminUser(Base):
    """管理端账号,与租户体系完全隔离(独立登录与 JWT audience)。"""

    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16))  # admin / ops / finance / readonly
    status: Mapped[str] = mapped_column(String(16), default="active")
    # 撤销闸:停用、改角色、改密都 +1,已签发的 token 立即失效
    token_version: Mapped[int] = mapped_column(default=0, server_default="0")
    # TOTP(admin/finance 强制):secret AES-GCM 加密(aad=f"totp:{id}");
    # recovery 为恢复码 bcrypt 哈希列表,用后作废
    totp_secret: Mapped[str | None] = mapped_column(String(255))
    totp_enabled: Mapped[bool] = mapped_column(default=False, server_default="false")
    totp_recovery: Mapped[list[str] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class AdminAdjustment(Base):
    """调账单:发起 → 第二管理员复核 → 生效。全程留痕。"""

    __tablename__ = "admin_adjustments"
    # 幂等键:响应丢失后重试不会开出第二张调账单(与充值订单同款的 (发起人, 键) 口径)
    __table_args__ = (UniqueConstraint("created_by", "idempotency_key"),)

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
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    reviewed_at: Mapped[datetime | None]
