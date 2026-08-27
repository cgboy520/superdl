"""image_node_cache 增加 cached_ref

Revision ID: 221434ad16fe
Revises: 7932c89f357e
Create Date: 2026-08-27 10:34:55.298013

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "221434ad16fe"
down_revision: str | None = "7932c89f357e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 记录该行缓存的是哪个 image_ref。ref 变更时 admin_update_image 会同事务删行,
    # 这一列是兜底:SQL 直改 / 数据修复脚本绕过服务层时,巡检据此作废重拉。
    # 存量行留 NULL = 不判定,保持原有行为。
    op.add_column("image_node_cache", sa.Column("cached_ref", sa.String(length=256), nullable=True))


def downgrade() -> None:
    op.drop_column("image_node_cache", "cached_ref")
