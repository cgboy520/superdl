from datetime import datetime, timedelta

from sqlalchemy import CheckConstraint, Index, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

DELETION_COOLDOWN = timedelta(days=7)


class User(Base):
    """用户账户;身份证号仅存脱敏串及带密钥摘要,不存原文。"""

    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_status_not_active", "status", postgresql_where=text("status <> 'active'")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(40), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="active")
    low_balance_warn_hours: Mapped[int] = mapped_column(default=24)
    token_version: Mapped[int] = mapped_column(default=0)
    verification_status: Mapped[str] = mapped_column(String(16), default="unverified")
    id_name: Mapped[str | None] = mapped_column(String(64))
    id_number: Mapped[str | None] = mapped_column(String(32))
    id_number_hmac: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    @property
    def tenant_ns(self) -> str:
        return f"tenant-{self.id}"


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


class SmsCode(Base):
    """一次性短信验证码;code_hash 仅存带密钥摘要,禁止明文入库。
    used_at = 任何作废(消费成功 / 失败 5 次 / 发送失败);consumed_at 只在校验成功时写。"""

    __tablename__ = "sms_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
    consumed_at: Mapped[datetime | None]
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
