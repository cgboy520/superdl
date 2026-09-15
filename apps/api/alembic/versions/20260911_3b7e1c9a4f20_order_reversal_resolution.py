"""orders 增加可空的 channel_reversal_resolved_at、channel_reversal_action 及动作约束。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3b7e1c9a4f20"
down_revision: str | None = "1d36973ae873"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("channel_reversal_resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "orders", sa.Column("channel_reversal_action", sa.String(length=16), nullable=True)
    )
    op.create_check_constraint(
        "reversal_action",
        "orders",
        "channel_reversal_action IS NULL OR channel_reversal_action IN ('release', 'chargeback')",
    )


def downgrade() -> None:
    raise RuntimeError("停机发布模型不支持回滚(fix-forward)")
