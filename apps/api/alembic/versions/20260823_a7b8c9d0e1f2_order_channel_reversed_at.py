"""order channel_reversed_at

Revision ID: a7b8c9d0e1f2
Revises: f1a2b3c4d5e6
Create Date: 2026-08-23 15:30:00.000000

已入账订单收到渠道关单/退款通知的时刻标记(异常清单 channel_reversed 分桶依据)。
可空列,存量订单恒 NULL。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders", sa.Column("channel_reversed_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("orders", "channel_reversed_at")
