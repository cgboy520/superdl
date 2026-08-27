"""档位收敛:mig / shared_std / shared_eco → shared,并摘掉旧业务唯一键

隔离机制的派发键改成节点池(见 app/core/gpu_adapter),档位只保留售卖分类:
- dedicated 不变(kata 池)
- mig / shared_std / shared_eco 一律并入 shared;标准 vs 经济由池派生
  (mig 池 = 硬切分标准档,hami 池 = 软切分经济档)

实例的 SKU 快照 `instances.spec.tier` 同步改写:它被 spec_to_gpu_request 读来构造
Pod,留着旧值会让存量实例重启时落到 fail-closed 分支。平台无生产库,一次改到位,
不在代码里留旧值别名。

新业务唯一键(带 pool_label / vcpu / mem_gb)由下一条迁移 CONCURRENTLY 建 —— 既有表
上的索引必须独立成文件走 autocommit_block(deploy/README.md「迁移向前兼容窗口」)。
autocommit_block 会先提交本条,因此本条开头先 fail-fast 探一遍「并档后会不会撞新键」:
撞了就在库还完整时报错退出,而不是等下一条建索引失败、把库停在「旧约束已摘、新索引没有」
的无保护窗口里。

商品重分类(upgrade 侧):存量 shared_std 落 hami 池,并档后展示为「共享·经济」;
存量 mig 落 mig 池,展示为「共享·标准」。价格与规格不变,变的是档位名与提示语。

Revision ID: bb37d098b158
Revises: 4388b481de73
Create Date: 2026-08-27 14:45:07.946880

"""

from collections.abc import Sequence

from alembic import op

revision: str = "bb37d098b158"
down_revision: str | None = "4388b481de73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 并入 shared 的三个旧档位值
_LEGACY_SHARED = "('mig', 'shared_std', 'shared_eco')"


# 并档后的新业务唯一键(与 catalog/models.py 的 uq_skus_business_key 同列同序)。
# GROUP BY 把 NULL 视为相等,与索引的 NULLS NOT DISTINCT 同口径。
_DUP_PROBE = """
DO $$
DECLARE dup text;
BEGIN
    SELECT string_agg(n, ', ') INTO dup FROM (
        SELECT min(name) AS n FROM skus
        GROUP BY gpu_model,
                 CASE WHEN tier IN ('mig', 'shared_std', 'shared_eco') THEN 'shared' ELSE tier END,
                 pool_label, mig_profile, gpu_cores_pct, vcpu, mem_gb
        HAVING count(*) > 1
    ) d;
    IF dup IS NOT NULL THEN
        RAISE EXCEPTION '档位并档后这些 SKU 会撞新业务唯一键,请先人工去重再迁移: %', dup;
    END IF;
END $$;
"""


def upgrade() -> None:
    # 撞键就在动任何数据之前退出(旧 shared_std 与 shared_eco 同落 hami 池,
    # 若只差 oversell 与价格,并档后七列全同)
    op.execute(_DUP_PROBE)
    # 先摘旧唯一键:旧键是 (型号, 档位, 切片, 算力份额),把三个档位并成一个会让
    # 「同型号同份额的 std 与 eco」在改写瞬间撞键
    op.execute("ALTER TABLE skus DROP CONSTRAINT IF EXISTS uq_skus_business_key")
    op.execute(f"UPDATE skus SET tier = 'shared' WHERE tier IN {_LEGACY_SHARED}")
    op.execute(
        "UPDATE instances SET spec = jsonb_set(spec, '{tier}', '\"shared\"')"
        f" WHERE spec->>'tier' IN {_LEGACY_SHARED}"
    )


def downgrade() -> None:
    # 档位并档不可逆(三个旧值并成一个,信息已丢失)。按池尽力还原:
    # 前提是库里没有「只差 vcpu/mem_gb 的两条 hami SKU」—— 新键允许、旧键不允许,
    # 有的话最后一步 ADD CONSTRAINT 会撞键,须先人工去重。
    # mig 池 → mig,hami 池 → shared_eco(std 与 eco 的区分无从恢复,统一落回 eco)。
    op.execute("UPDATE skus SET tier = 'mig' WHERE tier = 'shared' AND pool_label = 'mig'")
    op.execute("UPDATE skus SET tier = 'shared_eco' WHERE tier = 'shared' AND pool_label = 'hami'")
    op.execute(
        "UPDATE instances SET spec = jsonb_set(spec, '{tier}', '\"mig\"')"
        " WHERE spec->>'tier' = 'shared' AND spec->>'pool_label' = 'mig'"
    )
    op.execute(
        "UPDATE instances SET spec = jsonb_set(spec, '{tier}', '\"shared_eco\"')"
        " WHERE spec->>'tier' = 'shared' AND spec->>'pool_label' = 'hami'"
    )
    op.execute("ALTER TABLE skus DROP CONSTRAINT IF EXISTS uq_skus_business_key")
    op.execute(
        "ALTER TABLE skus ADD CONSTRAINT uq_skus_business_key"
        " UNIQUE NULLS NOT DISTINCT (gpu_model, tier, mig_profile, gpu_cores_pct)"
    )
