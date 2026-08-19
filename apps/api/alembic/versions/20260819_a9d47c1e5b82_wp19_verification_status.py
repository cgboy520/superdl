"""wp19 users.verification_status 实名认证状态

Revision ID: a9d47c1e5b82
Revises: f2b8d41a9c30
Create Date: 2026-08-19 20:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a9d47c1e5b82"
down_revision: str | None = "f2b8d41a9c30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "verification_status", sa.String(length=16), nullable=False, server_default="unverified"
        ),
    )
    op.alter_column("users", "verification_status", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "verification_status")
