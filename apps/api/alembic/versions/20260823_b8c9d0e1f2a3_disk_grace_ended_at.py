"""disk grace_ended_at

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-08-23 16:00:00.000000

数据盘记录最近一次回款恢复时刻:日结追平按 [grace_started, grace_ended) 区间
跳过宽限日(grace 不计费),边界日照常出账。可空列,存量盘恒 NULL(无区间可跳)。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "b8c9d0e1f2a3"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "data_disks", sa.Column("grace_ended_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("data_disks", "grace_ended_at")
