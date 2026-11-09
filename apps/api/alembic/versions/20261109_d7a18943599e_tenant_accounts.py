"""Tenant-created accounts and balance moves: users.username (unique when set), managed_org_id,
login_disabled, password_change_required; organizations.code (unique, the username prefix);
new table org_transfers; balance_ledger.ref_type admits org_transfer.

Data impact: existing organizations get the code org<id> (owners may change it while the tenant
has created no accounts); every other change only adds columns, a table and an allowed value.

Revision ID: d7a18943599e
Revises: 3ce9dbe034cf
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7a18943599e"
down_revision: str | None = "3ce9dbe034cf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(length=64), nullable=True))
    op.add_column("users", sa.Column("managed_org_id", sa.Integer(), nullable=True))
    op.add_column(
        "users",
        sa.Column("login_disabled", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "users",
        sa.Column("password_change_required", sa.Boolean(), server_default="false", nullable=False),
    )
    op.create_index(
        "uq_users_username",
        "users",
        ["username"],
        unique=True,
        postgresql_where=sa.text("username IS NOT NULL"),
    )
    op.create_index(op.f("ix_users_managed_org_id"), "users", ["managed_org_id"])

    op.add_column("organizations", sa.Column("code", sa.String(length=20), nullable=True))
    op.execute("UPDATE organizations SET code = 'org' || id")
    op.alter_column("organizations", "code", nullable=False)
    op.create_unique_constraint(op.f("uq_organizations_code"), "organizations", ["code"])

    op.create_table(
        "org_transfers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uuid", sa.String(length=32), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("from_user_id", sa.Integer(), nullable=False),
        sa.Column("to_user_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_org_transfers_amount_positive")),
        sa.CheckConstraint("kind IN ('allocate', 'reclaim')", name=op.f("ck_org_transfers_kind")),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_org_transfers_org_id_organizations")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_transfers")),
        sa.UniqueConstraint(
            "created_by",
            "idempotency_key",
            name=op.f("uq_org_transfers_created_by_idempotency_key"),
        ),
        sa.UniqueConstraint("uuid", name=op.f("uq_org_transfers_uuid")),
    )
    op.create_index(op.f("ix_org_transfers_org_id"), "org_transfers", ["org_id"])

    op.drop_constraint(op.f("ck_balance_ledger_ref_type"), "balance_ledger", type_="check")
    op.create_check_constraint(
        op.f("ck_balance_ledger_ref_type"),
        "balance_ledger",
        "ref_type IS NULL OR ref_type IN ('bill_hourly', 'bill_daily_disk', 'order', "
        "'adjustment', 'refund_request', 'subscription', 'org_transfer')",
    )


def downgrade() -> None:
    raise RuntimeError("downgrade is not supported: restore from backup")
