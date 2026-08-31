"""orders_backfill_idempotency_key_unique

人工补单幂等键落 DB 唯一约束(此前仅应用层同键重放判定,无并发兜底):
同一键被并发用到两笔订单时,约束拦住第二笔,应用层回查按 409 处理。
列可空,PG 唯一索引天然放行任意多个 NULL(未走补单的订单不受限)。

Revision ID: 6b3d9e1f5a07
Revises: 4d6e8f0a2b4c
Create Date: 2026-08-23 21:20:00.000000

"""

from collections.abc import Sequence

from alembic import op


revision: str = "6b3d9e1f5a07"
down_revision: str | None = "4d6e8f0a2b4c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 在线姿势:并发唯一索引 + USING INDEX 提升为约束(UNIQUE 无 NOT VALID 形态)
    with op.get_context().autocommit_block():
        op.create_index(
            "uq_orders_backfill_idempotency_key",
            "orders",
            ["backfill_idempotency_key"],
            unique=True,
            postgresql_concurrently=True,
        )
    op.execute(
        "ALTER TABLE orders ADD CONSTRAINT uq_orders_backfill_idempotency_key "
        "UNIQUE USING INDEX uq_orders_backfill_idempotency_key"
    )


def downgrade() -> None:
    op.drop_constraint("uq_orders_backfill_idempotency_key", "orders", type_="unique")
