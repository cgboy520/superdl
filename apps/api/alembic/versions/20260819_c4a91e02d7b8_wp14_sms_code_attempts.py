"""wp14 sms_codes.attempts 防爆破计次

Revision ID: c4a91e02d7b8
Revises: 271c4c44a161
Create Date: 2026-08-19 16:10:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c4a91e02d7b8"
down_revision: str | None = "271c4c44a161"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sms_codes",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    # 模型侧仅有 Python 默认值,补齐存量后去掉 server_default 保持一致
    op.alter_column("sms_codes", "attempts", server_default=None)


def downgrade() -> None:
    op.drop_column("sms_codes", "attempts")
