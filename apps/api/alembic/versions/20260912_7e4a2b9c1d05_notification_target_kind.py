"""notifications 增加可空的 target_kind。

user_id 非空的 gpu_fault 行回填 target_kind='tenant',target_id=user_id 的文本值。
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
    op.execute(
        "UPDATE notifications SET target_kind = 'tenant', target_id = user_id::text "
        "WHERE type = 'gpu_fault' AND user_id IS NOT NULL"
    )


def downgrade() -> None:
    raise RuntimeError("停机发布模型不支持回滚(fix-forward)")
