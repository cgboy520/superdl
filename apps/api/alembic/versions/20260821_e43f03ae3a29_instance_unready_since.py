"""instance unready_since

Revision ID: e43f03ae3a29
Revises: 9f48acb0ecbe
Create Date: 2026-08-21 21:14:21.530873

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "e43f03ae3a29"
down_revision: str | None = "9f48acb0ecbe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # running 实例 Pod 首次 not-ready 的时刻(节点失联判定用)。存量实例留 NULL:
    # 下一轮 reconcile 若发现它们 not-ready,会从那时开始计时。
    op.add_column(
        "instances", sa.Column("unready_since", sa.TIMESTAMP(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("instances", "unready_since")
