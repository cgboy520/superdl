"""cluster_status 补探测字段:nvidia RuntimeClass / 入口 / 证书 / 节点就绪面

Revision ID: 4388b481de73
Revises: 221434ad16fe
Create Date: 2026-08-27 13:28:56.206730

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "4388b481de73"
down_revision: str | None = "221434ad16fe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 全部带默认的可空转非空新列:存量单行(id=1)按默认落位,下一轮巡检(60s)即刷成实况
    op.add_column(
        "cluster_status",
        sa.Column("nvidia_runtimeclass", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "cluster_status",
        sa.Column("ingress_ready", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "cluster_status",
        sa.Column("cert_manager_ready", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "cluster_status", sa.Column("nodes_ready", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "cluster_status", sa.Column("nodes_total", sa.Integer(), server_default="0", nullable=False)
    )


def downgrade() -> None:
    op.drop_column("cluster_status", "nodes_total")
    op.drop_column("cluster_status", "nodes_ready")
    op.drop_column("cluster_status", "cert_manager_ready")
    op.drop_column("cluster_status", "ingress_ready")
    op.drop_column("cluster_status", "nvidia_runtimeclass")
