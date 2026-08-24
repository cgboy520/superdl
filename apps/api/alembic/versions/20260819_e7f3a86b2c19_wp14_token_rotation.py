"""wp14 token 撤销与轮换:users.token_version + used_refresh_tokens

Revision ID: e7f3a86b2c19
Revises: c4a91e02d7b8
Create Date: 2026-08-19 17:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e7f3a86b2c19"
down_revision: str | None = "c4a91e02d7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("users", "token_version", server_default=None)
    op.create_table(
        "used_refresh_tokens",
        sa.Column("jti", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "used_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("jti", name=op.f("pk_used_refresh_tokens")),
    )
    op.create_index(
        op.f("ix_used_refresh_tokens_expires_at"),
        "used_refresh_tokens",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_used_refresh_tokens_user_id"), "used_refresh_tokens", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_used_refresh_tokens_user_id"), table_name="used_refresh_tokens")
    op.drop_index(op.f("ix_used_refresh_tokens_expires_at"), table_name="used_refresh_tokens")
    op.drop_table("used_refresh_tokens")
    op.drop_column("users", "token_version")
