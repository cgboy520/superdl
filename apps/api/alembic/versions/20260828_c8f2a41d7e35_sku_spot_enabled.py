"""skus.spot_enabled

竞价档的上架开关。默认关,与 period_enabled 相反:竞价的对价是「可被回收」,
那是要在下单前跟用户讲清楚的承诺,不该因为新建了一条 SKU 就自动生效。

Revision ID: c8f2a41d7e35
Revises: a7c1d5e94b02
Create Date: 2026-08-28 01:22:41.905133

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8f2a41d7e35"
down_revision: str | None = "a7c1d5e94b02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "skus", sa.Column("spot_enabled", sa.Boolean(), server_default="false", nullable=False)
    )


def downgrade() -> None:
    op.drop_column("skus", "spot_enabled")
