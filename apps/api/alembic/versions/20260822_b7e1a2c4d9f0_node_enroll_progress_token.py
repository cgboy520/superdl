"""node enroll progress token

Revision ID: b7e1a2c4d9f0
Revises: d380ee16a1e1
Create Date: 2026-08-22 03:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "b7e1a2c4d9f0"
down_revision: str | None = "d380ee16a1e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """注册令牌一次性化:首次 bootstrap 换发的 progress 令牌哈希列。

    存量行该列为 NULL,语义=「升级前创建的旧行」:仍允许注册令牌重复
    bootstrap 与进度上报,保证正在装机/重启续跑的节点不被升级掐断。
    """
    op.add_column(
        "node_enrollments",
        sa.Column("progress_token_hash", sa.String(64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_node_enrollments_progress_token_hash",
        "node_enrollments",
        ["progress_token_hash"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_node_enrollments_progress_token_hash", "node_enrollments", type_="unique"
    )
    op.drop_column("node_enrollments", "progress_token_hash")
