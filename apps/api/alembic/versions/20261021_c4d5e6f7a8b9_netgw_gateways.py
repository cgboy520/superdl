"""Private-network gateways: net_gateways and net_gateway_forwards (new tables, no data impact).

Revision ID: c4d5e6f7a8b9
Revises: b2c3d4e5f6a7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4d5e6f7a8b9"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "net_gateways",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uuid", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("protocol", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("secrets_encrypted", sa.Text(), nullable=False),
        sa.Column("public_key", sa.String(length=64), nullable=True),
        sa.Column("generation", sa.Integer(), server_default="1", nullable=False),
        sa.Column("applied_generation", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tunnel_up", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("tunnel_changed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("pod_phase", sa.String(length=32), nullable=True),
        sa.Column("checked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "protocol IN ('wireguard', 'ipsec', 'openvpn', 'openconnect')",
            name=op.f("ck_net_gateways_protocol"),
        ),
        sa.CheckConstraint("status IN ('active', 'deleting')", name=op.f("ck_net_gateways_status")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_net_gateways")),
        sa.UniqueConstraint(
            "user_id", "idempotency_key", name=op.f("uq_net_gateways_user_id_idempotency_key")
        ),
        sa.UniqueConstraint("user_id", "name", name=op.f("uq_net_gateways_user_id_name")),
        sa.UniqueConstraint("uuid", name=op.f("uq_net_gateways_uuid")),
    )
    op.create_index(op.f("ix_net_gateways_status"), "net_gateways", ["status"])
    op.create_index(op.f("ix_net_gateways_user_id"), "net_gateways", ["user_id"])
    op.create_table(
        "net_gateway_forwards",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("gateway_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("target_host", sa.String(length=253), nullable=True),
        sa.Column("target_port", sa.Integer(), nullable=False),
        sa.Column("instance_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "kind IN ('intranet', 'instance')", name=op.f("ck_net_gateway_forwards_kind")
        ),
        sa.CheckConstraint(
            "(kind = 'intranet') = (target_host IS NOT NULL AND instance_id IS NULL)",
            name=op.f("ck_net_gateway_forwards_kind_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["gateway_id"],
            ["net_gateways.id"],
            name=op.f("fk_net_gateway_forwards_gateway_id_net_gateways"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_net_gateway_forwards")),
        sa.UniqueConstraint("user_id", "name", name=op.f("uq_net_gateway_forwards_user_id_name")),
    )
    op.create_index(
        op.f("ix_net_gateway_forwards_gateway_id"), "net_gateway_forwards", ["gateway_id"]
    )
    op.create_index(
        op.f("ix_net_gateway_forwards_instance_id"), "net_gateway_forwards", ["instance_id"]
    )
    op.create_index(op.f("ix_net_gateway_forwards_user_id"), "net_gateway_forwards", ["user_id"])
    op.create_index(
        "uq_net_gateway_forwards_listen_port",
        "net_gateway_forwards",
        ["gateway_id", "port"],
        unique=True,
        postgresql_where=sa.text("kind = 'instance'"),
    )


def downgrade() -> None:
    raise RuntimeError("downgrade is not supported: restore from backup")
