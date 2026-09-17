from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AdminUser(Base):
    """Admin account with its own login and JWT audience.

    The TOTP secret is stored encrypted, recovery codes as bcrypt hashes; the accepted timestep
    must advance monotonically under the row lock.
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
    """Adjustment: initiated → reviewed by a second admin → effective."""

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
