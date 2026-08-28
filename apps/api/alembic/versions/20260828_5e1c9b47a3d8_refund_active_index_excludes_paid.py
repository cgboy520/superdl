"""退款活跃口径收窄:已打款不再占位(同单可多次部分退款)

uq_refund_requests_active_order 的谓词由 (pending, approved, paid) 收窄为
(pending, approved):同单累计可退语义下,已打款行不再挡下一笔部分退款,
超额防护由申请/打款两处按 Σpaid 复核(见 refunds.create_refund / payout_refund)。

上线前置:存量库确认无「同单两条进行中」即兼容(旧口径更严,天然满足)。

Revision ID: 5e1c9b47a3d8
Revises: 0f3b8a6d2e91
Create Date: 2026-08-28 16:35:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5e1c9b47a3d8"
down_revision: str | None = "0f3b8a6d2e91"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "status IN ('pending', 'approved', 'paid')"
_NEW = "status IN ('pending', 'approved')"


def upgrade() -> None:
    op.drop_index(
        "uq_refund_requests_active_order",
        table_name="refund_requests",
        postgresql_where=sa.text(_OLD),
    )
    op.create_index(
        "uq_refund_requests_active_order",
        "refund_requests",
        ["order_no"],
        unique=True,
        postgresql_where=sa.text(_NEW),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_refund_requests_active_order",
        table_name="refund_requests",
        postgresql_where=sa.text(_NEW),
    )
    op.create_index(
        "uq_refund_requests_active_order",
        "refund_requests",
        ["order_no"],
        unique=True,
        postgresql_where=sa.text(_OLD),
    )
