"""External CephFS storage: system disks and data disks become subvolumes of the external Ceph
cluster (instances.system_disk_path / system_disk_bytes_used / disk_usage_checked_at,
data_disks.ceph_path / bytes_used / usage_checked_at), cluster_status.storage_classes is replaced
by ceph_csi_ready, new single-row table storage_status. Every disk of a user is mounted on all of
that user's instances: data_disks.mount_path (default /data, unique per user among non-deleted
disks) replaces the exclusive data_disks.mounted_instance_id, instances.data_disk_ids (the
snapshot an instance's Pod mounts) replaces instances.data_disk_id. Nodes keep no instance storage,
so node_enrollments.nvme_devices (the local volume group devices) is dropped.

Hard cut-over: node-local instance disks (TopoLVM) and in-cluster data disks (Rook) are not
migrated. The upgrade refuses to run while any instance is not released or any data disk is not
deleted; release them (or move their data out) first. Data impact once it runs: none (the guard
guarantees no live volume exists); the cluster_status.storage_classes column is dropped and is
refilled as ceph_csi_ready by the next node patrol; the NVMe device lists of node enrollments are
dropped.

Revision ID: c26a95423081
Revises: a8b9c0d1e2f3
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c26a95423081"
down_revision: str | None = "a8b9c0d1e2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _refuse_live_local_volumes() -> None:
    bind = op.get_bind()
    instances = bind.execute(
        sa.text("SELECT count(*) FROM instances WHERE status <> 'released'")
    ).scalar_one()
    disks = bind.execute(
        sa.text("SELECT count(*) FROM data_disks WHERE status <> 'deleted'")
    ).scalar_one()
    if instances or disks:
        raise RuntimeError(
            "external Ceph storage cut-over: "
            f"{instances} instance(s) not released and {disks} data disk(s) not deleted still "
            "live on node-local / in-cluster storage; release or delete them, then upgrade again"
        )


def upgrade() -> None:
    _refuse_live_local_volumes()
    op.add_column("instances", sa.Column("system_disk_path", sa.String(length=255), nullable=True))
    op.add_column("instances", sa.Column("system_disk_bytes_used", sa.BigInteger(), nullable=True))
    op.add_column(
        "instances",
        sa.Column("disk_usage_checked_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column("data_disks", sa.Column("ceph_path", sa.String(length=255), nullable=True))
    op.add_column("data_disks", sa.Column("bytes_used", sa.BigInteger(), nullable=True))
    op.add_column(
        "data_disks", sa.Column("usage_checked_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.drop_index(op.f("ix_data_disks_mounted_instance_id"), table_name="data_disks")
    op.drop_index("uq_data_disks_mounted_instance", table_name="data_disks")
    op.drop_column("data_disks", "mounted_instance_id")
    op.add_column(
        "data_disks",
        sa.Column("mount_path", sa.String(length=64), server_default="/data", nullable=False),
    )
    op.create_index(
        "uq_data_disks_user_mount_path",
        "data_disks",
        ["user_id", "mount_path"],
        unique=True,
        postgresql_where=sa.text("status <> 'deleted'"),
    )
    op.drop_column("instances", "data_disk_id")
    op.add_column(
        "instances",
        sa.Column(
            "data_disk_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )
    op.drop_column("cluster_status", "storage_classes")
    op.drop_column("node_enrollments", "nvme_devices")
    op.add_column(
        "cluster_status",
        sa.Column("ceph_csi_ready", sa.Boolean(), server_default="false", nullable=False),
    )
    op.create_table(
        "storage_status",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("group_bytes_used", sa.BigInteger(), nullable=True),
        sa.Column("group_bytes_quota", sa.BigInteger(), nullable=True),
        sa.Column("checked_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_storage_status")),
    )


def downgrade() -> None:
    raise RuntimeError("downgrade is not supported: restore from backup")
