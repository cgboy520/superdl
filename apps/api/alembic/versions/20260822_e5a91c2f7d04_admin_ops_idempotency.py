"""admin 调账/补单幂等键

Revision ID: e5a91c2f7d04
Revises: 3c5b13cface6
Create Date: 2026-08-22 12:30:00.000000

调账发起与人工补单支持 Idempotency-Key:
- admin_adjustments 加 (created_by, idempotency_key) 唯一约束(与充值订单同口径);
- orders 加 backfill_idempotency_key 标记列(不设唯一约束:仅作同键重放判定,
  不同订单复用同键不应互相阻塞)。
NULL 在 PG 唯一约束下互不冲突,存量行与不带键的调用不受影响。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "e5a91c2f7d04"
down_revision: str | None = "3c5b13cface6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "admin_adjustments", sa.Column("idempotency_key", sa.String(length=64), nullable=True)
    )
    # 在线姿势:并发唯一索引 + USING INDEX 提升为约束(UNIQUE 无 NOT VALID 形态)
    with op.get_context().autocommit_block():
        op.create_index(
            "uq_admin_adjustments_created_by_idempotency_key",
            "admin_adjustments",
            ["created_by", "idempotency_key"],
            unique=True,
            postgresql_concurrently=True,
        )
    op.execute(
        "ALTER TABLE admin_adjustments "
        "ADD CONSTRAINT uq_admin_adjustments_created_by_idempotency_key "
        "UNIQUE USING INDEX uq_admin_adjustments_created_by_idempotency_key"
    )
    op.add_column(
        "orders", sa.Column("backfill_idempotency_key", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("orders", "backfill_idempotency_key")
    op.drop_constraint(
        op.f("uq_admin_adjustments_created_by_idempotency_key"),
        "admin_adjustments",
        type_="unique",
    )
    op.drop_column("admin_adjustments", "idempotency_key")
