from datetime import datetime, timedelta

from sqlalchemy import CheckConstraint, Index, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

DELETION_COOLDOWN = timedelta(days=7)


class User(Base):
    """User account. Login handles: `email` (lower-cased) and/or `phone` (E.164), each unique when
    set; both become NULL on account deletion. KYC stores only the masked identity number and a
    keyed digest, never the plaintext."""

    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_status_not_active", "status", postgresql_where=text("status <> 'active'")),
        Index("uq_users_email", "email", unique=True, postgresql_where=text("email IS NOT NULL")),
        Index("uq_users_phone", "phone", unique=True, postgresql_where=text("phone IS NOT NULL")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str | None] = mapped_column(String(254))
    email_verified_at: Mapped[datetime | None]
    phone: Mapped[str | None] = mapped_column(String(20))
    password_hash: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="active")
    low_balance_warn_hours: Mapped[int] = mapped_column(default=24)
    token_version: Mapped[int] = mapped_column(default=0)
    kyc_status: Mapped[str] = mapped_column(String(16), default="unverified")
    kyc_name: Mapped[str | None] = mapped_column(String(128))
    kyc_identity_masked: Mapped[str | None] = mapped_column(String(32))
    kyc_identity_hmac: Mapped[str | None] = mapped_column(String(64), index=True)
    kyc_provider: Mapped[str | None] = mapped_column(String(32))
    kyc_ref: Mapped[str | None] = mapped_column(String(128))
    kyc_verified_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    @property
    def tenant_ns(self) -> str:
        return f"tenant-{self.id}"

    @property
    def primary_handle(self) -> str:
        """Email first, then phone; deleted accounts fall back to `user:<id>`."""
        return self.email or self.phone or f"user:{self.id}"


class UserQuotaOverride(Base):
    """用户级配额覆盖;配额字段为 None 时使用平台运行时配置。"""

    __tablename__ = "user_quota_overrides"

    user_id: Mapped[int] = mapped_column(primary_key=True)
    max_gpus: Mapped[int | None]
    max_instances: Mapped[int | None]
    max_disks: Mapped[int | None]
    note: Mapped[str] = mapped_column(String(200))
    updated_by: Mapped[int]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class SshKey(Base):
    __tablename__ = "ssh_keys"
    __table_args__ = (UniqueConstraint("user_id", "fingerprint"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(64))
    public_key: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class UsedRefreshToken(Base):
    """refresh 消费记录及刷新宽限窗内可重建的轮换结果。"""

    __tablename__ = "used_refresh_tokens"

    jti: Mapped[str] = mapped_column(String(32), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(index=True)
    used_at: Mapped[datetime] = mapped_column(server_default=func.now())
    consumed_via: Mapped[str | None] = mapped_column(String(16))
    replaced_refresh_jti: Mapped[str | None] = mapped_column(String(32))
    replaced_access_jti: Mapped[str | None] = mapped_column(String(32))
    replaced_iat: Mapped[datetime | None]


class AccountDeletionRequest(Base):
    """账号注销申请;每用户至多一条 pending,冷静期由 DELETION_COOLDOWN 定义。"""

    __tablename__ = "account_deletion_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'completed', 'rejected', 'cancelled')",
            name="status",
        ),
        Index(
            "uq_account_deletion_requests_pending_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    reason: Mapped[str] = mapped_column(String(256))
    requested_at: Mapped[datetime] = mapped_column(server_default=func.now())
    processed_by: Mapped[int | None]
    processed_at: Mapped[datetime | None]
    note: Mapped[str | None] = mapped_column(String(512))

    @property
    def cooldown_ends_at(self) -> datetime:
        return self.requested_at + DELETION_COOLDOWN


class VerificationCode(Base):
    """One-time code sent to an email or phone target; `code_hash` is a keyed digest only.
    used_at = voided for any reason (consumed / 5 failures / delivery failure); consumed_at is set
    only on successful verification."""

    __tablename__ = "verification_codes"
    __table_args__ = (Index("ix_verification_codes_channel_target", "channel", "target"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(8))
    target: Mapped[str] = mapped_column(String(254))
    code_hash: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(24))
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
    consumed_at: Mapped[datetime | None]
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
