"""settlement gaps and reconcile checkpoints

结算缺口登记表(追平截断/死信跳窗留痕)+ 资金核对增量游标表(链式校验断点续扫)。

Revision ID: c1f7a92e5b38
Revises: b7e1a2c4d9f0
Create Date: 2026-08-22 13:10:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c1f7a92e5b38"
down_revision: str | None = "b7e1a2c4d9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "settlement_gaps",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("window_start", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("object_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_settlement_gaps")),
        sa.UniqueConstraint(
            "kind",
            "window_start",
            "object_id",
            name=op.f("uq_settlement_gaps_kind_window_start_object_id"),
        ),
    )
    op.create_table(
        "reconcile_checkpoints",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("last_ledger_id", sa.BigInteger(), nullable=False),
        sa.Column("balance_after", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_reconcile_checkpoints")),
    )


def downgrade() -> None:
    op.drop_table("reconcile_checkpoints")
    op.drop_table("settlement_gaps")
