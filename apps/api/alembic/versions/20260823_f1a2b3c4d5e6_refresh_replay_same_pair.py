"""refresh replay same pair

Revision ID: f1a2b3c4d5e6
Revises: 9f2c4a6b8e15
Create Date: 2026-08-23 14:10:00.000000

宽限窗重放须回首次轮换的同一对 token:消费记录登记替代对的 jti 与签发时刻。
全部为可空列,存量消费记录(含登出产生的)保持 NULL,走原补发语义。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "9f2c4a6b8e15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "used_refresh_tokens",
        sa.Column("replaced_refresh_jti", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "used_refresh_tokens",
        sa.Column("replaced_access_jti", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "used_refresh_tokens", sa.Column("replaced_iat", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("used_refresh_tokens", "replaced_iat")
    op.drop_column("used_refresh_tokens", "replaced_access_jti")
    op.drop_column("used_refresh_tokens", "replaced_refresh_jti")
