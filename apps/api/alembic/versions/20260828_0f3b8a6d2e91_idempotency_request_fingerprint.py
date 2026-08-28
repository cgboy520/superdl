"""创建类五表落库请求指纹(幂等键异参检测)

同键重放时比对 request_fingerprint,异参显式 409(对齐 Stripe 惯例,
参照 admin_adjustments.request_fingerprint 的既有模式)。老行为 NULL,
应用层视为兼容不比对;列只写不查,不建索引。

Revision ID: 0f3b8a6d2e91
Revises: b4e7d1a92c06
Create Date: 2026-08-28 16:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0f3b8a6d2e91"
down_revision: str | None = "b4e7d1a92c06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("orders", "instances", "data_disks", "refund_requests", "invoice_requests")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("request_fingerprint", sa.String(64), nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "request_fingerprint")
