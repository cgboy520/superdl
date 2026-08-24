"""port allocation blocked

Revision ID: d380ee16a1e1
Revises: 32de653d3d22
Create Date: 2026-08-21 23:09:39.541765

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "d380ee16a1e1"
down_revision: str | None = "32de653d3d22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """端口池的 blocked 标记。

    端口池 30000–32767 与 K8s NodePort 是同一段,集群内 registry 之类的对象会硬占其中
    某些端口。没有这一列时,分配器在「最小空闲」耗尽后一路取 max+1:每次都算出同一个
    被占端口、每次 Service 创建都 422、事务整体回滚 —— 那行 PortAllocation 从来没提交过,
    高水位线也就永远停在它前面,此后所有触顶的新建实例全部失败,人工不介入无法恢复。
    """
    op.add_column(
        "port_allocations",
        sa.Column("blocked", sa.Boolean(), nullable=False, server_default="false"),
    )
    # 已知占用先入库(与 Settings.ssh_port_excluded 同源;那边是运行期跳过,这边是数据兜底)
    op.execute(
        "INSERT INTO port_allocations (port, instance_id, blocked) VALUES (30500, NULL, true) "
        "ON CONFLICT (port) DO UPDATE SET blocked = true"
    )


def downgrade() -> None:
    op.execute("DELETE FROM port_allocations WHERE blocked")
    op.drop_column("port_allocations", "blocked")
