"""subscriptions.request_fingerprint:订阅幂等键的异参检测

转换(按量转包周期)与续费共用 UNIQUE(user_id, idempotency_key) 一个命名空间,且此前
两条重放查询都没带指纹:同一把键打向另一台实例时,重放会返回**另一台**实例的订阅并回
200 + X-Idempotent-Replay,而目标实例既没转成包周期也没扣款——用户被告知买成了,其实没有。
落指纹后同键异参一律 409(与订单/退款/发票/实例/数据盘同口径)。

expand-only:可空列、无默认;存量行 NULL 在 find_replay 的严格比对下即视为异参(409),
不会静默把老单当成重放。

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-01 10:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subscriptions", sa.Column("request_fingerprint", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("subscriptions", "request_fingerprint")
