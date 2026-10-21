"""Private-network gateway L3 attachments: net_gateway_attachments (new table, no data impact).

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: str | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "net_gateway_attachments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("gateway_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("instance_id", sa.Integer(), nullable=False),
        sa.Column("address", sa.String(length=15), nullable=False),
        sa.Column("public_key", sa.String(length=44), nullable=False),
        sa.Column("private_key_encrypted", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["gateway_id"],
            ["net_gateways.id"],
            name=op.f("fk_net_gateway_attachments_gateway_id_net_gateways"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_net_gateway_attachments")),
        sa.UniqueConstraint(
            "gateway_id", "address", name=op.f("uq_net_gateway_attachments_gateway_id_address")
        ),
        sa.UniqueConstraint("instance_id", name=op.f("uq_net_gateway_attachments_instance_id")),
    )
    op.create_index(
        op.f("ix_net_gateway_attachments_gateway_id"), "net_gateway_attachments", ["gateway_id"]
    )
    op.create_index(
        op.f("ix_net_gateway_attachments_user_id"), "net_gateway_attachments", ["user_id"]
    )


def downgrade() -> None:
    raise RuntimeError("downgrade is not supported: restore from backup")
