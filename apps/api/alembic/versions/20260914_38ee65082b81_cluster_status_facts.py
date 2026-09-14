"""cluster_status 加 pools_ready 与 component_facts。

pools_ready:池→Ready 且可调度的节点数,档位可用性判据从原始池成员数改为它。
component_facts:体检项 key → 探测事实(状态 / 主数字 / 事实行 / 对象表),
组件体检面板与诊断抽屉的数据源;布尔列保留不动,下发门禁仍读它们。
数据影响:两列均可空,存量行留 NULL,巡检下一轮(60s)写满;
期间体检项按 unknown 渲染。

Revision ID: 38ee65082b81
Revises: cb327e579688
Create Date: 2026-09-14 16:32:05.472309

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "38ee65082b81"
down_revision: str | None = "cb327e579688"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "cluster_status",
        sa.Column("pools_ready", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "cluster_status",
        sa.Column("component_facts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    raise RuntimeError("停机发布模型不支持回滚(fix-forward)")
