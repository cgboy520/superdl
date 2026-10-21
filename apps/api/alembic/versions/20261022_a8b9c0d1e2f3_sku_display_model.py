"""SKU display model: skus.display_model, the GPU model tenants see (NULL shows gpu_model). No data
impact: existing rows get NULL and keep showing gpu_model.

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8b9c0d1e2f3"
down_revision: str | None = "f7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("skus", sa.Column("display_model", sa.String(length=32), nullable=True))


def downgrade() -> None:
    raise RuntimeError("downgrade is not supported: restore from backup")
