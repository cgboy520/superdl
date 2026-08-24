"""announcements_idempotency_key

公告发布幂等键:此前 dedup_key 以 announcement.id 为前缀,HTTP 层重试会产生
新 id → 新去重域 → 全体租户收到重复公告。键落 DB 唯一约束,同键重放返回
原公告;并发同键由约束兜底,应用层回查按重放处理。列可空,PG 唯一索引
天然放行任意多个 NULL(不带键的历史行与调用不受影响)。

Revision ID: a7c8e2f4b6d1
Revises: 6b3d9e1f5a07
Create Date: 2026-08-24 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "a7c8e2f4b6d1"
down_revision: str | None = "6b3d9e1f5a07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "announcements", sa.Column("idempotency_key", sa.String(length=64), nullable=True)
    )
    op.create_unique_constraint(
        "uq_announcements_idempotency_key", "announcements", ["idempotency_key"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_announcements_idempotency_key", "announcements", type_="unique")
    op.drop_column("announcements", "idempotency_key")
