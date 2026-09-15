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
    """法务文档版本;每文档、语言与版本号唯一,每文档与语言至多一条 published。"""

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
    doc_key: Mapped[str] = mapped_column(String(32))
    locale: Mapped[str] = mapped_column(String(16))
    version: Mapped[int]
    title: Mapped[str] = mapped_column(String(128))
    content_md: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    effective_note: Mapped[str | None] = mapped_column(String(512))
    created_by: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    published_by: Mapped[int | None]
    published_at: Mapped[datetime | None]


class UserConsent(Base):
    """用户同意的法务文档版本、时间与客户端 IP。"""

    __tablename__ = "user_consents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    doc_key: Mapped[str] = mapped_column(String(32))
    version: Mapped[int]
    accepted_at: Mapped[datetime] = mapped_column(server_default=func.now())
    client_ip: Mapped[str | None] = mapped_column(String(45))
