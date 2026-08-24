"""user_quota_overrides 用户级配额覆盖(F9)

Revision ID: 16b6aefc6c67
Revises: 2cabf755d3fc
Create Date: 2026-08-23 21:57:31.176396

校验链 override → policy → env:三个配额列均可空,NULL = 该维走默认链。
note/updated_by 必填:谁为什么改的,运营留痕(审计明细在 audit_log)。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "16b6aefc6c67"
down_revision: str | None = "2cabf755d3fc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_quota_overrides",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("max_gpus", sa.Integer(), nullable=True),
        sa.Column("max_instances", sa.Integer(), nullable=True),
        sa.Column("max_disks", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_quota_overrides")),
    )


def downgrade() -> None:
    op.drop_table("user_quota_overrides")
