"""Custom images users upload: new table custom_images (chunked upload state, import result,
registry reference); node_specs.arch records each node's CPU architecture. No data impact.

Revision ID: aeef4be1c935
Revises: d7a18943599e
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "aeef4be1c935"
down_revision: str | None = "d7a18943599e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts(name: str, *, now: bool = False, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.TIMESTAMP(timezone=True),
        server_default=sa.func.now() if now else None,
        nullable=nullable,
    )


def upgrade() -> None:
    op.add_column("node_specs", sa.Column("arch", sa.String(length=16), nullable=True))
    op.create_table(
        "custom_images",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uuid", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("tag", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=True),
        sa.Column("shared_with_org", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("archive_format", sa.String(length=8), nullable=False),
        sa.Column("upload_bytes", sa.BigInteger(), nullable=False),
        sa.Column("chunk_bytes", sa.Integer(), nullable=False),
        sa.Column("chunks_total", sa.Integer(), nullable=False),
        sa.Column("received_bitmap", sa.LargeBinary(), nullable=False),
        sa.Column("first_chunk_sha256", sa.String(length=64), nullable=False),
        _ts("upload_expires_at"),
        sa.Column("progress_bytes", sa.BigInteger(), server_default="0", nullable=False),
        _ts("import_started_at", nullable=True),
        _ts("import_finished_at", nullable=True),
        sa.Column("error_key", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("image_ref", sa.String(length=256), nullable=True),
        sa.Column("digest", sa.String(length=80), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("unpacked_bytes", sa.BigInteger(), nullable=True),
        sa.Column("architecture", sa.String(length=16), nullable=True),
        sa.Column("os", sa.String(length=16), nullable=True),
        sa.Column("entrypoint", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("cmd", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("exposed_ports", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("dev_ready", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("layers", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
        _ts("created_at", now=True),
        _ts("updated_at", now=True),
        sa.CheckConstraint(
            "archive_format IN ('tar', 'tar.gz', 'zip')",
            name=op.f("ck_custom_images_archive_format"),
        ),
        sa.CheckConstraint(
            "status IN ('uploading', 'importing', 'ready', 'failed', 'deleting')",
            name=op.f("ck_custom_images_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_custom_images")),
        sa.UniqueConstraint(
            "user_id",
            "idempotency_key",
            name=op.f("uq_custom_images_user_id_idempotency_key"),
        ),
        sa.UniqueConstraint(
            "user_id", "name", "tag", name=op.f("uq_custom_images_user_id_name_tag")
        ),
        sa.UniqueConstraint("uuid", name=op.f("uq_custom_images_uuid")),
    )
    op.create_index(op.f("ix_custom_images_image_ref"), "custom_images", ["image_ref"])
    op.create_index(op.f("ix_custom_images_status"), "custom_images", ["status"])
    op.create_index(op.f("ix_custom_images_user_id"), "custom_images", ["user_id"])


def downgrade() -> None:
    raise RuntimeError("downgrade is not supported: restore from backup")
