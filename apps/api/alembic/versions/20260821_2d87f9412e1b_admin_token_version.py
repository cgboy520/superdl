"""admin token_version

Revision ID: 2d87f9412e1b
Revises: e43f03ae3a29
Create Date: 2026-08-21 21:45:44.212928

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "2d87f9412e1b"
down_revision: str | None = "e43f03ae3a29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 管理端撤销闸。存量账号从 0 起算,与新签发 token 的 ver 一致,不会把在线管理员踢下线。
    op.add_column(
        "admin_users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("admin_users", "token_version")
