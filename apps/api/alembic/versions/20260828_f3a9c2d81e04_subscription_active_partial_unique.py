"""subscriptions 一实例仅一行 active 的部分唯一索引

续费并发竞态(手动 × 自动)的 DB 兜底:应用层由「钱包行锁 + 续费行锁(FOR UPDATE)」
串行化(见 app.modules.billing.subscriptions.renew),本索引兜住任何绕过该路径的写入
(管理端修数、未来的新 worker),从根上杜绝「一实例两行 active」导致的重复扣款。

上线前置:存量库先跑
  SELECT instance_id FROM subscriptions WHERE status='active'
  GROUP BY instance_id HAVING count(*) > 1;
确认无重复 active 行再执行本迁移(当前不变量由钱包行锁维持,预期为零行)。

Revision ID: f3a9c2d81e04
Revises: e2b6f0c47a19
Create Date: 2026-08-28 14:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a9c2d81e04"
down_revision: str | None = "e2b6f0c47a19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 在线建部分唯一索引:CONCURRENTLY 不锁写;CONCURRENTLY 不能在事务块内,
    # 必须 autocommit_block(env.py 整轮单事务)
    with op.get_context().autocommit_block():
        op.create_index(
            "uq_subscriptions_active_instance",
            "subscriptions",
            ["instance_id"],
            unique=True,
            postgresql_where=sa.text("status = 'active'"),
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "uq_subscriptions_active_instance",
            table_name="subscriptions",
            postgresql_where=sa.text("status = 'active'"),
            postgresql_concurrently=True,
        )
