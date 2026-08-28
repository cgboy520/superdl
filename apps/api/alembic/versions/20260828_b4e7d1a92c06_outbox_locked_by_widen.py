"""outbox_tasks.locked_by 放宽到 128

worker_id = `<hostname>-<pid>`(K8s hostname 即 Pod 名,DNS label 上限 63),
再叠 lane 后缀可破 64 —— claim 的 commit 抛 StringDataRightTruncation,
所有 lane 共享同一前缀时该组件 outbox 整体静默停摆。
应用层已加 make_worker_id 截断 + outbox_loop 启动 fail-fast(workers/main.py),
列宽放宽到 128 作冗余层。

Revision ID: b4e7d1a92c06
Revises: f3a9c2d81e04
Create Date: 2026-08-28 14:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4e7d1a92c06"
down_revision: str | None = "f3a9c2d81e04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "outbox_tasks",
        "locked_by",
        type_=sa.String(length=128),
        existing_type=sa.String(length=64),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "outbox_tasks",
        "locked_by",
        type_=sa.String(length=64),
        existing_type=sa.String(length=128),
        existing_nullable=True,
    )
