"""wp16 policy_overrides 策略参数在线调整

Revision ID: f2b8d41a9c30
Revises: e7f3a86b2c19
Create Date: 2026-08-19 18:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f2b8d41a9c30"
down_revision: str | None = "e7f3a86b2c19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "policy_overrides",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.String(length=64), nullable=False),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_policy_overrides")),
    )


def downgrade() -> None:
    op.drop_table("policy_overrides")
