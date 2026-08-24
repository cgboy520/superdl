"""disk idempotency key

Revision ID: aa76ffcba115
Revises: 2d87f9412e1b
Create Date: 2026-08-21 22:10:11.492948

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "aa76ffcba115"
down_revision: str | None = "2d87f9412e1b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 建盘此前完全没有幂等保护:响应丢失时用户按第二下就多出一块按日计费的孤儿盘。
    # NULL 在 PG 的唯一约束下互不冲突,所以存量行与不带 Idempotency-Key 的调用都不受影响。
    op.add_column("data_disks", sa.Column("idempotency_key", sa.String(length=64), nullable=True))
    op.create_unique_constraint(
        op.f("uq_data_disks_user_id_idempotency_key"),
        "data_disks",
        ["user_id", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint(op.f("uq_data_disks_user_id_idempotency_key"), "data_disks", type_="unique")
    op.drop_column("data_disks", "idempotency_key")
