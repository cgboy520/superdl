"""数据盘改为一盘一 PVC:配额即 PVC 容量,不再有 JuiceFS 子路径与目录配额下发。

juicefs_subpath 去掉(PVC 名按 uuid 算,见 core/k8s/base.data_disk_pvc_name);
quota_synced 改名 provisioned(语义从「目录配额已下发」变为「PVC 已建/扩到位」)。
数据影响:两列连同唯一约束一并删除,存量盘的 provisioned 落到 false,
由 reconciler 的 disk.provision 死信重派补齐;删列不可逆。

Revision ID: cb327e579688
Revises: 8f1c3d5e2a46
Create Date: 2026-09-14 14:25:35.825626

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "cb327e579688"
down_revision: str | None = "8f1c3d5e2a46"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "data_disks",
        sa.Column("provisioned", sa.Boolean(), server_default="false", nullable=False),
    )
    # 存量 active 盘的 PVC 由 disk.provision 建出,这里不假定已就绪
    op.drop_constraint(op.f("uq_data_disks_juicefs_subpath"), "data_disks", type_="unique")
    op.drop_column("data_disks", "quota_synced")
    op.drop_column("data_disks", "juicefs_subpath")


def downgrade() -> None:
    raise RuntimeError("停机发布模型不支持回滚(fix-forward)")
