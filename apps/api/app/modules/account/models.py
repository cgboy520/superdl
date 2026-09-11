from datetime import datetime, timedelta

from sqlalchemy import CheckConstraint, Index, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# 注销冷静期:pending 满 7 天后管理端才可执行
DELETION_COOLDOWN = timedelta(days=7)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 注销匿名化后改写为 del:{id}:{随机 16 hex},故宽于 20
    phone: Mapped[str] = mapped_column(String(40), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active / frozen / deleted
    low_balance_warn_hours: Mapped[int] = mapped_column(default=24)  # 余额预警阈值(用户可设)
    token_version: Mapped[int] = mapped_column(default=0)  # 撤销闸:+1 即失效全部在外 token
    verification_status: Mapped[str] = mapped_column(String(16), default="unverified")
    # 实名字段
    id_name: Mapped[str | None] = mapped_column(String(64))
    id_number: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    @property
    def tenant_ns(self) -> str:
        return f"tenant-{self.id}"


class UserQuotaOverride(Base):
    """用户级配额覆盖:任一字段为 None = 该维走默认链(policy → env)。"""

    __tablename__ = "user_quota_overrides"

    user_id: Mapped[int] = mapped_column(primary_key=True)
    max_gpus: Mapped[int | None]
    max_instances: Mapped[int | None]
    max_disks: Mapped[int | None]
    note: Mapped[str] = mapped_column(String(200))  # 覆盖原因(必填,运营留痕)
    updated_by: Mapped[int]  # admin_users.id
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class SshKey(Base):
    __tablename__ = "ssh_keys"
    # 指纹按 (用户, 指纹) 唯一,不做全局唯一
    __table_args__ = (UniqueConstraint("user_id", "fingerprint"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(64))
    public_key: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64))  # SHA256:base64
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class UsedRefreshToken(Base):
    """refresh token 一次性消费记录(轮换):jti 重放触发全量撤销。"""

    __tablename__ = "used_refresh_tokens"

    jti: Mapped[str] = mapped_column(String(32), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(index=True)  # 清理依据
    used_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # 消费途径:refresh(轮换)/ logout(登出)。登出消费的重放只回 401,不全撤
    consumed_via: Mapped[str | None] = mapped_column(String(16))
    # 首消费登记的轮换结果:宽限窗内同 jti 重放回同一对 token;登出消费为 NULL
    replaced_refresh_jti: Mapped[str | None] = mapped_column(String(32))
    replaced_access_jti: Mapped[str | None] = mapped_column(String(32))
    replaced_iat: Mapped[datetime | None]


class AccountDeletionRequest(Base):
    """账号注销申请。

    状态机:pending → completed(冷静期满且校验通过)/ cancelled(用户撤销)
    / rejected(驳回或执行前校验不过)。
    部分唯一索引:每用户至多一条 pending。
    """

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
    processed_by: Mapped[int | None]  # admin_users.id
    processed_at: Mapped[datetime | None]
    note: Mapped[str | None] = mapped_column(String(512))  # 驳回理由 / 自动驳回的残留清单

    @property
    def cooldown_ends_at(self) -> datetime:
        return self.requested_at + DELETION_COOLDOWN


class SmsCode(Base):
    __tablename__ = "sms_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    # 带密钥摘要,禁止明文入库(见 core.crypto.hash_sms_code)
    code_hash: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(16))  # register / login / reset_password
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
    attempts: Mapped[int] = mapped_column(default=0)  # 校验失败计次,达上限即作废
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
