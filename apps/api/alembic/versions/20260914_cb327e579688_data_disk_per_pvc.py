"""data_disks 新增非空 provisioned 列,默认值为 false。

删除 quota_synced、juicefs_subpath 及后者的唯一约束;旧列数据不回填到 provisioned。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "cb327e579688"
down_revision: str | None = "8f1c3d5e2a46"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "data_disks",
        sa.Column("provisioned", sa.Boolean(), server_default="false", nullable=False),
    )
    op.drop_constraint(op.f("uq_data_disks_juicefs_subpath"), "data_disks", type_="unique")
    op.drop_column("data_disks", "quota_synced")
    op.drop_column("data_disks", "juicefs_subpath")


def downgrade() -> None:
    raise RuntimeError("停机发布模型不支持回滚(fix-forward)")
