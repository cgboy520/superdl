"""SKU 业务唯一键补上 pool_label / vcpu / mem_gb

新键 (gpu_model, tier, pool_label, mig_profile, gpu_cores_pct, vcpu, mem_gb):
- 带 pool_label:档位收敛后「共享」既可能落 mig 池(硬切分)也可能落 hami 池(软切分),
  同型号同份额的两条 SKU 是两件不同的商品
- 带 vcpu / mem_gb:同一张卡要能出不同配套规格;纯 CPU 规格更是只能靠这两列区分
  (它的 gpu_model 为空、gpu 三列全 0,不带这两列就全平台只能存在一条)

既有表上建索引,按 deploy/README.md「迁移向前兼容窗口」独立成文件走 autocommit_block。
IF NOT EXISTS 让 CONCURRENTLY 中途失败后可重跑;真留下 INVALID 索引须先手工 DROP 再重跑
(CONCURRENTLY 的已知形态,见同章)。

Revision ID: 97ffb93a8ecb
Revises: bb37d098b158
Create Date: 2026-08-27 14:50:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "97ffb93a8ecb"
down_revision: str | None = "bb37d098b158"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_skus_business_key"
            " ON skus (gpu_model, tier, pool_label, mig_profile, gpu_cores_pct, vcpu, mem_gb)"
            " NULLS NOT DISTINCT"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS uq_skus_business_key")
