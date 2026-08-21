from datetime import datetime
from decimal import Decimal

from sqlalchemy import Numeric, String, func
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
    # 撤销闸:停用、改角色、改密都 +1,已签发的 token 立即失效。
    # 与用户端 users.token_version 同形 —— 管理端能改价、封号、改支付凭据,
    # 泄露一个 token 却只能等 TTL 到期是不可接受的。
    token_version: Mapped[int] = mapped_column(default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class AdminAdjustment(Base):
    """调账单:发起 → 第二管理员复核 → 生效。全程留痕。"""

    __tablename__ = "admin_adjustments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))  # 带符号:正=补偿,负=扣减
    reason: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    # pending / approved / rejected
    created_by: Mapped[int]  # admin_users.id
    reviewed_by: Mapped[int | None]
    review_comment: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    reviewed_at: Mapped[datetime | None]
