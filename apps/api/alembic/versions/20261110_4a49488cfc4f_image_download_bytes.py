"""Platform image download size: images.download_bytes (compressed bytes per CPU architecture,
read from the registry by the image size patrol) and images.download_ref (the reference they were
measured for). No data impact.

Revision ID: 4a49488cfc4f
Revises: aeef4be1c935
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "4a49488cfc4f"
down_revision: str | None = "aeef4be1c935"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "images",
        sa.Column("download_bytes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("images", sa.Column("download_ref", sa.String(length=256), nullable=True))


def downgrade() -> None:
    raise RuntimeError("downgrade is not supported: restore from backup")
