"""Organization gateways: net_gateways.org_id; the (user_id, name) unique constraint becomes a
partial unique index over personal gateways plus (org_id, name) over organization gateways; an
organization gateway must carry an L3 configuration. No data impact: existing rows are personal
(org_id NULL) and keep the same name uniqueness.

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7a8b9c0d1e2"
down_revision: str | None = "e6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("net_gateways", sa.Column("org_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_net_gateways_org_id"), "net_gateways", ["org_id"])
    op.drop_constraint(op.f("uq_net_gateways_user_id_name"), "net_gateways", type_="unique")
    op.create_index(
        "uq_net_gateways_user_id_name",
        "net_gateways",
        ["user_id", "name"],
        unique=True,
        postgresql_where=sa.text("org_id IS NULL"),
    )
    op.create_index(
        "uq_net_gateways_org_id_name",
        "net_gateways",
        ["org_id", "name"],
        unique=True,
        postgresql_where=sa.text("org_id IS NOT NULL"),
    )
    op.create_check_constraint(
        op.f("ck_net_gateways_org_l3"),
        "net_gateways",
        "org_id IS NULL OR jsonb_typeof(config -> 'l3') = 'object'",
    )


def downgrade() -> None:
    raise RuntimeError("downgrade is not supported: restore from backup")
