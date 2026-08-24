"""node_specs desired unschedulable

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-08-23 17:00:00.000000

cordon/uncordon 期望态落台账:outbox 乱序重试按 payload 执行会把旧意图盖回去,
handler 与巡检统一读期望态收敛。可空列,NULL = 无期望(从未 cordon 过)。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "c9d0e1f2a3b4"
down_revision: str | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("node_specs", sa.Column("desired_unschedulable", sa.Boolean(), nullable=True))
    op.add_column("node_specs", sa.Column("desired_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("node_specs", "desired_at")
    op.drop_column("node_specs", "desired_unschedulable")
