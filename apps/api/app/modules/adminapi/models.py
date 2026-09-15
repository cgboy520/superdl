from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AdminUser(Base):
    """独立登录与 JWT audience 的管理员账号。

    TOTP 密钥加密存储,恢复码存 bcrypt 哈希;已验证 timestep 须在行锁内单调推进。
    """

    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="active")
    token_version: Mapped[int] = mapped_column(default=0, server_default="0")
    totp_secret: Mapped[str | None] = mapped_column(String(255))
    totp_enabled: Mapped[bool] = mapped_column(default=False, server_default="false")
    totp_recovery: Mapped[list[str] | None] = mapped_column(JSONB)
    last_totp_timestep: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class AdminAdjustment(Base):
    """调账单:发起 → 第二管理员复核 → 生效。"""

    __tablename__ = "admin_adjustments"
    __table_args__ = (
        UniqueConstraint(
            "created_by", "user_id", "idempotency_key", name="uq_admin_adjustments_idem_scope"
        ),
        CheckConstraint("amount <> 0", name="amount_nonzero"),
        CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="status"),
        CheckConstraint(
            "reviewed_by IS NULL OR reviewed_by <> created_by", name="reviewer_not_creator"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    reason: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_by: Mapped[int]
    reviewed_by: Mapped[int | None]
    review_comment: Mapped[str | None] = mapped_column(String(256))
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    reviewed_at: Mapped[datetime | None]
