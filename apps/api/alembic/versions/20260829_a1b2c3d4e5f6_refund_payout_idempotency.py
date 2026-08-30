"""refund_requests 打款登记幂等键

打款是全站唯一出金动作,双击/重试/响应丢失重放的保护此前反而最弱(申请键
idempotency_key 已被用户创建占用,不能复用)。本迁移加两列:
payout_idempotency_key / payout_request_fingerprint —— 管理端打款重放命中
(同键同参且已 paid)返回 200 + X-Idempotent-Replay,同键异参 409。
并发安全由既有行锁(_get_for_update FOR UPDATE)保证,无需额外唯一约束。

Revision ID: a1b2c3d4e5f6
Revises: a3f8c1d94e02
Create Date: 2026-08-29 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "a3f8c1d94e02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "refund_requests", sa.Column("payout_idempotency_key", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "refund_requests",
        sa.Column("payout_request_fingerprint", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("refund_requests", "payout_request_fingerprint")
    op.drop_column("refund_requests", "payout_idempotency_key")
