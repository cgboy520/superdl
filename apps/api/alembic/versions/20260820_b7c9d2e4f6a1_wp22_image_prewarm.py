"""wp22 镜像预热产品化:is_prewarmed 改名 prewarm_enabled + image_node_cache 表

Revision ID: b7c9d2e4f6a1
Revises: d3e6a92c8f14
Create Date: 2026-08-20 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "b7c9d2e4f6a1"
down_revision: str | None = "d3e6a92c8f14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 语义演进:静态"已预热"标记 → 管理员意图"参与预热";真实状态由 image_node_cache 聚合。
    # 改名是纯 catalog 操作(不重写表、不扫行),images 是管理端低频小表
    op.alter_column(  # ddl-risk: reviewed —— 纯 catalog 改名,小表瞬时锁
        "images", "is_prewarmed", new_column_name="prewarm_enabled"
    )
    op.create_table(
        "image_node_cache",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("image_id", sa.Integer(), nullable=False),
        sa.Column("node_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("checked_at", sa.TIMESTAMP(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["image_id"],
            ["images.id"],
            name=op.f("fk_image_node_cache_image_id_images"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_image_node_cache")),
        sa.UniqueConstraint(
            "image_id", "node_name", name=op.f("uq_image_node_cache_image_id_node_name")
        ),
    )
    op.create_index(
        op.f("ix_image_node_cache_image_id"), "image_node_cache", ["image_id"], unique=False
    )
    op.create_index(
        op.f("ix_image_node_cache_status"), "image_node_cache", ["status"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_image_node_cache_status"), table_name="image_node_cache")
    op.drop_index(op.f("ix_image_node_cache_image_id"), table_name="image_node_cache")
    op.drop_table("image_node_cache")
    op.alter_column("images", "prewarm_enabled", new_column_name="is_prewarmed")
