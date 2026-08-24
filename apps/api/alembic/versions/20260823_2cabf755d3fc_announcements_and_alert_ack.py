"""announcements_and_alert_ack:F6 公告管理 + F8 告警闭环

- 新表 announcements:公告级记录(发布/撤回/触达人数);用户端触达仍走
  notifications fanout(dedup_key = ann:{announcement_id}:{user_id})。
- notifications 加 status(published/revoked,仅 announcement 类型会被撤回,
  存量行 server_default published 语义不变)。
- notifications 加 acked_by/acked_at:管理端告警流(admin_alerts)确认留痕。

Revision ID: 2cabf755d3fc
Revises: cd2abcccda26
Create Date: 2026-08-23 20:55:31.604367

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "2cabf755d3fc"
down_revision: str | None = "cd2abcccda26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "announcements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reached", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("revoked_by", sa.Integer(), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=256), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_announcements")),
        sa.CheckConstraint(
            "status IN ('published', 'revoked')", name=op.f("ck_announcements_status")
        ),
    )
    op.add_column(
        "notifications",
        sa.Column("status", sa.String(length=16), server_default="published", nullable=False),
    )
    op.add_column("notifications", sa.Column("acked_by", sa.Integer(), nullable=True))
    op.add_column(
        "notifications", sa.Column("acked_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("notifications", "acked_at")
    op.drop_column("notifications", "acked_by")
    op.drop_column("notifications", "status")
    op.drop_table("announcements")
