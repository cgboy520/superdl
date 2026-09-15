"""sms_codes 增加可空 consumed_at 列;存量按 used_at 且 attempts 未达上限回填。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1f2c3d4e5b6"
down_revision: str | None = "c8437eb43a7a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sms_codes", sa.Column("consumed_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.execute(
        "UPDATE sms_codes SET consumed_at = used_at WHERE used_at IS NOT NULL AND attempts < 5"
    )


def downgrade() -> None:
    raise RuntimeError("停机发布模型不支持回滚(fix-forward)")
