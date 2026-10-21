"""Organizations: organizations, org_members, org_invitations (new tables, no data impact).

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6f7a8b9c0d1"
down_revision: str | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts(name: str, *, now: bool = False, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.TIMESTAMP(timezone=True),
        server_default=sa.func.now() if now else None,
        nullable=nullable,
    )


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uuid", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
        _ts("created_at", now=True),
        _ts("dissolved_at", nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'dissolved')", name=op.f("ck_organizations_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
        sa.UniqueConstraint(
            "created_by",
            "idempotency_key",
            name=op.f("uq_organizations_created_by_idempotency_key"),
        ),
        sa.UniqueConstraint("uuid", name=op.f("uq_organizations_uuid")),
    )
    op.create_index(op.f("ix_organizations_created_by"), "organizations", ["created_by"])
    op.create_index(op.f("ix_organizations_status"), "organizations", ["status"])
    op.create_table(
        "org_invitations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uuid", sa.String(length=32), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("role", sa.String(length=8), nullable=False),
        sa.Column("remark", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("invited_by", sa.Integer(), nullable=False),
        _ts("created_at", now=True),
        _ts("expires_at"),
        _ts("responded_at", nullable=True),
        sa.CheckConstraint("role IN ('admin', 'member')", name=op.f("ck_org_invitations_role")),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'declined', 'revoked', 'expired')",
            name=op.f("ck_org_invitations_status"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_org_invitations_org_id_organizations")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_invitations")),
        sa.UniqueConstraint("uuid", name=op.f("uq_org_invitations_uuid")),
    )
    op.create_index(
        "ix_org_invitations_email_pending",
        "org_invitations",
        ["email"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(op.f("ix_org_invitations_org_id"), "org_invitations", ["org_id"])
    op.create_index(
        "uq_org_invitations_pending",
        "org_invitations",
        ["org_id", "email"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        "org_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=8), nullable=False),
        sa.Column("remark", sa.String(length=64), nullable=True),
        sa.Column("invited_by", sa.Integer(), nullable=True),
        sa.Column("consent_version", sa.String(length=16), nullable=False),
        _ts("joined_at", now=True),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'member')", name=op.f("ck_org_members_role")
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_org_members_org_id_organizations")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_members")),
        sa.UniqueConstraint("user_id", name=op.f("uq_org_members_user_id")),
    )
    op.create_index(op.f("ix_org_members_org_id"), "org_members", ["org_id"])
    op.create_index(
        "uq_org_members_owner",
        "org_members",
        ["org_id"],
        unique=True,
        postgresql_where=sa.text("role = 'owner'"),
    )


def downgrade() -> None:
    raise RuntimeError("downgrade is not supported: restore from backup")
