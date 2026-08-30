"""notifications 记结构化跳转目标(通知中心深链)

target_id: 行点击后的精确跳转目标——instance 类为实例 uuid,ticket 类为工单 id;
balance/account/announcement 等无目标类型恒空。老行 NULL,前端按类型回落列表页。

Revision ID: a3f8c1d94e02
Revises: 7d2a4f18c6b5
Create Date: 2026-08-28 23:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3f8c1d94e02"
down_revision: str | None = "7d2a4f18c6b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("notifications", sa.Column("target_id", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("notifications", "target_id")
