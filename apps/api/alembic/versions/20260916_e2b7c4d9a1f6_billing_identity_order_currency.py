"""billing_identity singleton + orders.currency.

Existing orders are backfilled with CNY (the only currency the platform could settle in before
this revision). The identity row is seeded as CNY / Asia/Shanghai only when money rows exist,
so an upgraded deployment refuses to boot until SUPERDL_PLATFORM_CURRENCY / SUPERDL_BILLING_TIMEZONE
match; a fresh database locks whatever the first boot's env says.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2b7c4d9a1f6"
down_revision: str | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "billing_identity",
        sa.Column("id", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_billing_identity_singleton")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_billing_identity")),
    )
    op.add_column("orders", sa.Column("currency", sa.String(length=3), nullable=True))
    op.execute("UPDATE orders SET currency = 'CNY'")
    op.alter_column("orders", "currency", nullable=False)
    op.execute(
        """
        INSERT INTO billing_identity (id, currency, timezone, locked_at)
        SELECT 1, 'CNY', 'Asia/Shanghai', now()
        WHERE EXISTS (SELECT 1 FROM balance_ledger) OR EXISTS (SELECT 1 FROM orders)
           OR EXISTS (SELECT 1 FROM bills_hourly) OR EXISTS (SELECT 1 FROM bills_daily_disk)
        """
    )


def downgrade() -> None:
    raise RuntimeError("停机发布模型不支持回滚(fix-forward)")
