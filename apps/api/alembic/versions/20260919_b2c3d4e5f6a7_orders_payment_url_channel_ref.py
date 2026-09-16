"""orders.qr_url becomes payment_url (checkout URLs are longer than QR payloads) and gains
channel_ref for the channel-side session/intent reference of redirect channels.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("orders", "qr_url", new_column_name="payment_url")
    op.alter_column(
        "orders",
        "payment_url",
        existing_type=sa.String(length=512),
        type_=sa.String(length=2048),
        existing_nullable=True,
    )
    op.add_column("orders", sa.Column("channel_ref", sa.String(length=128), nullable=True))


def downgrade() -> None:
    raise RuntimeError("downgrade is not supported: restore from backup")
