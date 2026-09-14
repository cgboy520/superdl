"""node_specs 加 desired_pool(期望池)。

管理端切池写入的期望态,与 desired_unschedulable 同款:handler 与巡检阶段 C2 按它收敛
节点池标签与 GPU operand 标签。非空即覆盖 node_enrollments.pool 作为池事实源,切完不清空。
数据影响:可空列,存量行留 NULL,巡检仍以注册登记为池事实源,行为不变。

Revision ID: c8437eb43a7a
Revises: 38ee65082b81
Create Date: 2026-09-14 21:20:21.362497

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8437eb43a7a"
down_revision: str | None = "38ee65082b81"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("node_specs", sa.Column("desired_pool", sa.String(length=8), nullable=True))


def downgrade() -> None:
    raise RuntimeError("停机发布模型不支持回滚(fix-forward)")
