"""node_specs 增加可空的 desired_pool VARCHAR(8) 列,存量行留 NULL。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8437eb43a7a"
down_revision: str | None = "38ee65082b81"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("node_specs", sa.Column("desired_pool", sa.String(length=8), nullable=True))


def downgrade() -> None:
    raise RuntimeError("停机发布模型不支持回滚(fix-forward)")
