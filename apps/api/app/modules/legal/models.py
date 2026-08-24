from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class LegalDocVersion(Base):
    """法务文档版本(F7)。(doc_key, locale, version) 唯一;
    部分唯一索引保证每 (doc_key, locale) 至多一条 published(发布事务把旧版转 archived)。

    published_by/published_at 仅 published 落;预置版本发布人留空(系统预置)。
    """

    __tablename__ = "legal_doc_versions"
    __table_args__ = (
        UniqueConstraint("doc_key", "locale", "version"),
        CheckConstraint("status IN ('draft', 'published', 'archived')", name="status"),
        Index(
            "uq_legal_doc_versions_one_published",
            "doc_key",
            "locale",
            unique=True,
            postgresql_where=text("status = 'published'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_key: Mapped[str] = mapped_column(String(32))  # terms / privacy / deletion_notice,可扩展
    locale: Mapped[str] = mapped_column(String(16))  # zh-CN / en-US
    version: Mapped[int]
    title: Mapped[str] = mapped_column(String(128))
    content_md: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    effective_note: Mapped[str | None] = mapped_column(String(512))  # 生效说明(可空)
    created_by: Mapped[int | None]  # admin_users.id;预置为空
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    published_by: Mapped[int | None]  # admin_users.id;预置为空
    published_at: Mapped[datetime | None]


class UserConsent(Base):
    """注册同意存证(F7,合规举证):注册必勾时按当前 published 版本落 terms/privacy 各一条。"""

    __tablename__ = "user_consents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    doc_key: Mapped[str] = mapped_column(String(32))
    version: Mapped[int]
    accepted_at: Mapped[datetime] = mapped_column(server_default=func.now())
    client_ip: Mapped[str | None] = mapped_column(String(45))  # IPv6 最长 45
