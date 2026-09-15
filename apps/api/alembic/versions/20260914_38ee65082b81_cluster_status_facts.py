"""cluster_status 增加可空 JSONB 列 pools_ready 与 component_facts,存量行留 NULL。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "38ee65082b81"
down_revision: str | None = "cb327e579688"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "cluster_status",
        sa.Column("pools_ready", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "cluster_status",
        sa.Column("component_facts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    raise RuntimeError("停机发布模型不支持回滚(fix-forward)")
