"""drop_redundant_bill_indexes

Revision ID: 3c5b13cface6
Revises: 8f3a1c9e2b47
Create Date: 2026-08-22 19:39:47.467682

"""

from collections.abc import Sequence

from alembic import op


revision: str = "3c5b13cface6"
down_revision: str | None = "8f3a1c9e2b47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 两列已被各自唯一约束(instance_id, hour_start)/(disk_id, day)的前导列覆盖,单列索引是纯写放大
    op.drop_index("ix_bills_hourly_instance_id", table_name="bills_hourly")
    op.drop_index("ix_bills_daily_disk_disk_id", table_name="bills_daily_disk")


def downgrade() -> None:
    op.create_index("ix_bills_daily_disk_disk_id", "bills_daily_disk", ["disk_id"])
    op.create_index("ix_bills_hourly_instance_id", "bills_hourly", ["instance_id"])
