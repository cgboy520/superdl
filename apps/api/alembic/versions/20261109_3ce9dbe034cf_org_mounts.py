"""Organization storage mounts: new table org_mounts (a Ceph directory, the organization's own
encrypted cephx key, access, revision, check status). No data impact.

Revision ID: 3ce9dbe034cf
Revises: c26a95423081
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3ce9dbe034cf"
down_revision: str | None = "c26a95423081"
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
        "org_mounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uuid", sa.String(length=32), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("ceph_path", sa.String(length=255), nullable=False),
        sa.Column("ceph_user", sa.String(length=64), nullable=False),
        sa.Column("key_encrypted", sa.Text(), nullable=False),
        sa.Column("read_only", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        _ts("checked_at", nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        _ts("created_at", now=True),
        _ts("updated_at", now=True),
        sa.CheckConstraint(
            "status IN ('checking', 'ready', 'failed')", name=op.f("ck_org_mounts_status")
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_org_mounts_org_id_organizations")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_mounts")),
        sa.UniqueConstraint("org_id", "name", name=op.f("uq_org_mounts_org_id_name")),
        sa.UniqueConstraint("uuid", name=op.f("uq_org_mounts_uuid")),
    )
    op.create_index(op.f("ix_org_mounts_org_id"), "org_mounts", ["org_id"])


def downgrade() -> None:
    raise RuntimeError("downgrade is not supported: restore from backup")
