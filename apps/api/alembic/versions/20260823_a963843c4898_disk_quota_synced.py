"""disk_quota_synced:数据盘 JuiceFS 目录配额下发状态(W1-1)

存量盘 server_default=false → reconciler 对账环自动补发配额(滚动发布即回填);
新盘创建即入队 disk.quota 任务,下发成功才置 true。

Revision ID: a963843c4898
Revises: e71e3183b13a
Create Date: 2026-08-23 13:03:17.363180

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "a963843c4898"
down_revision: str | None = "e71e3183b13a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "data_disks",
        sa.Column("quota_synced", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("data_disks", "quota_synced")
