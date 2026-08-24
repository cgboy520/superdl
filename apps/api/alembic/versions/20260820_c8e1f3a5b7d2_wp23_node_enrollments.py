"""wp23 node_enrollments 节点注册令牌与加入进度

Revision ID: c8e1f3a5b7d2
Revises: b7c9d2e4f6a1
Create Date: 2026-08-20 16:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c8e1f3a5b7d2"
down_revision: str | None = "b7c9d2e4f6a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "node_enrollments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("pool", sa.String(length=8), nullable=False),
        sa.Column("hostname", sa.String(length=253), nullable=True),
        sa.Column("note", sa.String(length=128), nullable=True),
        sa.Column("nvme_devices", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("node_name", sa.String(length=253), nullable=True),
        sa.Column("reported_ip", sa.String(length=64), nullable=True),
        sa.Column("os_info", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("gpu_info", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_report_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("joined_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_node_enrollments")),
        sa.UniqueConstraint(
            "created_by",
            "idempotency_key",
            name=op.f("uq_node_enrollments_created_by_idempotency_key"),
        ),
        sa.UniqueConstraint("token_hash", name=op.f("uq_node_enrollments_token_hash")),
    )
    op.create_index(
        op.f("ix_node_enrollments_status"), "node_enrollments", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_node_enrollments_node_name"), "node_enrollments", ["node_name"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_node_enrollments_node_name"), table_name="node_enrollments")
    op.drop_index(op.f("ix_node_enrollments_status"), table_name="node_enrollments")
    op.drop_table("node_enrollments")
