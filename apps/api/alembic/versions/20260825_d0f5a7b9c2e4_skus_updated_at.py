"""skus_updated_at

skus 补 updated_at(server_default=now() + ORM onupdate):近似库存缓存签名
(行数, max(updated_at)) 需要它感知 SKU 改 型号/池/上下架——此前签名只看节点台账,
SKU 变更只能靠 admin_update_sku 手工 clear_cache 兜底,其余变更路径会展示过期库存。
SKU 是配置表(行数极小),加带非易失 server_default 的列不重写表,无锁窗风险。

Revision ID: d0f5a7b9c2e4
Revises: c9e4f6a8b1d3
Create Date: 2026-08-25 11:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "d0f5a7b9c2e4"
down_revision: str | None = "c9e4f6a8b1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "skus",
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("skus", "updated_at")
