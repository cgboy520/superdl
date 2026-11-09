"""Custom images: Docker archives users upload in chunks, imported into the registry's user
project as `<project>/u<user id>/<name>:<tag>`. One row per upload; the name:tag pair is unique
per user until the row is gone."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

STATUS_UPLOADING = "uploading"
STATUS_IMPORTING = "importing"
STATUS_READY = "ready"
STATUS_FAILED = "failed"
STATUS_DELETING = "deleting"


class CustomImage(Base):
    __tablename__ = "custom_images"
    __table_args__ = (
        CheckConstraint(
            "status IN ('uploading', 'importing', 'ready', 'failed', 'deleting')", name="status"
        ),
        CheckConstraint("archive_format IN ('tar', 'tar.gz', 'zip')", name="archive_format"),
        UniqueConstraint("user_id", "name", "tag"),
        UniqueConstraint("user_id", "idempotency_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column(String(32), unique=True)
    user_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(64))
    tag: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(String(200))
    shared_with_org: Mapped[bool] = mapped_column(default=False, server_default="false")
    status: Mapped[str] = mapped_column(String(16), default=STATUS_UPLOADING, index=True)
    filename: Mapped[str] = mapped_column(String(255))
    archive_format: Mapped[str] = mapped_column(String(8))
    upload_bytes: Mapped[int] = mapped_column(BigInteger)
    chunk_bytes: Mapped[int]
    chunks_total: Mapped[int]
    received_bitmap: Mapped[bytes] = mapped_column(LargeBinary)
    first_chunk_sha256: Mapped[str] = mapped_column(String(64))
    upload_expires_at: Mapped[datetime]
    progress_bytes: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    import_started_at: Mapped[datetime | None]
    import_finished_at: Mapped[datetime | None]
    error_key: Mapped[str | None] = mapped_column(String(64))
    error_detail: Mapped[str | None] = mapped_column(Text)
    image_ref: Mapped[str | None] = mapped_column(String(256), index=True)
    digest: Mapped[str | None] = mapped_column(String(80))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    unpacked_bytes: Mapped[int | None] = mapped_column(BigInteger)
    architecture: Mapped[str | None] = mapped_column(String(16))
    os: Mapped[str | None] = mapped_column(String(16))
    entrypoint: Mapped[list[str] | None] = mapped_column(JSONB)
    cmd: Mapped[list[str] | None] = mapped_column(JSONB)
    exposed_ports: Mapped[list[Any] | None] = mapped_column(JSONB)
    dev_ready: Mapped[bool] = mapped_column(default=False, server_default="false")
    layers: Mapped[int | None]
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
