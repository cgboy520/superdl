"""admin_totp_mfa:admin/finance 强制 TOTP 绑定

- totp_secret:AES-GCM 加密存储(aad=f"totp:{admin_id}")
- totp_recovery:恢复码 bcrypt 哈希列表(jsonb),用后作废

Revision ID: e71e3183b13a
Revises: c9d0e1f2a3b4
Create Date: 2026-08-23 12:03:25.088089

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "e71e3183b13a"
down_revision: str | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("admin_users", sa.Column("totp_secret", sa.String(255), nullable=True))
    op.add_column(
        "admin_users",
        sa.Column("totp_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "admin_users",
        sa.Column("totp_recovery", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("admin_users", "totp_recovery")
    op.drop_column("admin_users", "totp_enabled")
    op.drop_column("admin_users", "totp_secret")
