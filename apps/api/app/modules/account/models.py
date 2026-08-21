from datetime import datetime

from sqlalchemy import String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active / frozen
    low_balance_warn_hours: Mapped[int] = mapped_column(default=24)  # 余额预警阈值(用户可设)
    token_version: Mapped[int] = mapped_column(default=0)  # 撤销闸:+1 即失效全部在外 token
    verification_status: Mapped[str] = mapped_column(String(16), default="unverified")
    # 实名与企业/开票字段
    id_name: Mapped[str | None] = mapped_column(String(64))
    id_number: Mapped[str | None] = mapped_column(String(32))
    company_name: Mapped[str | None] = mapped_column(String(128))
    company_tax_id: Mapped[str | None] = mapped_column(String(32))
    invoice_title: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    @property
    def tenant_ns(self) -> str:
        return f"tenant-{self.id}"


class SshKey(Base):
    __tablename__ = "ssh_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(64))
    public_key: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)  # SHA256:base64
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class UsedRefreshToken(Base):
    """refresh token 一次性消费记录(轮换):jti 重放 = 疑似泄露,触发全量撤销。"""

    __tablename__ = "used_refresh_tokens"

    jti: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    expires_at: Mapped[datetime] = mapped_column(index=True)  # 过期即可清理
    used_at: Mapped[datetime] = mapped_column(server_default=func.now())


class SmsCode(Base):
    __tablename__ = "sms_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    # 带密钥摘要,禁止明文入库(见 core.crypto.hash_sms_code)
    code_hash: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(16))  # register / login
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
    attempts: Mapped[int] = mapped_column(default=0)  # 校验失败计次,达上限即作废
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
