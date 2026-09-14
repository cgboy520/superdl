"""notifications 增加 target_kind(管理端告警流深链种类),写入侧给全;
历史 gpu_fault 行按 user_id 回填。

Revision ID: 7e4a2b9c1d05
Revises: 6d2f8a1c0b77
Create Date: 2026-09-12 09:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7e4a2b9c1d05"
down_revision: str | None = "6d2f8a1c0b77"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("notifications", sa.Column("target_kind", sa.String(length=16), nullable=True))
    # 历史行只回填可确定的一类:gpu_fault 的租户归属;其余告警行无深链
    op.execute(
        "UPDATE notifications SET target_kind = 'tenant', target_id = user_id::text "
        "WHERE type = 'gpu_fault' AND user_id IS NOT NULL"
    )


def downgrade() -> None:
    raise RuntimeError("停机发布模型不支持回滚(fix-forward)")
