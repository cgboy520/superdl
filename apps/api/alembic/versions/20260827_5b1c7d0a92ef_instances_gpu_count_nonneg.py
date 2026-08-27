"""instances.gpu_count 加非负 CHECK(CPU 实例 gpu_count=0 成为合法值)

纯 CPU 实例落库时 gpu_count=0,契约层的下界随之从 ge=1 放开到 ge=0。放开之后
「负卡数」不再被类型边界挡住:计费份数 billing_units(gpu_count) 会对负数抛错,
但账单口径不该指望应用层不出 bug —— 手工 SQL / 数据修复脚本绕得过应用层。

NOT VALID 建约束(只校验新写入,不扫存量、不拿全表校验锁),再单独 VALIDATE
(只拿 SHARE UPDATE EXCLUSIVE,不阻塞读写)。存量行 gpu_count 恒 ≥1,VALIDATE 必过。

Revision ID: 5b1c7d0a92ef
Revises: 97ffb93a8ecb
Create Date: 2026-08-27 16:20:11.482913

"""

from collections.abc import Sequence

from alembic import op

revision: str = "5b1c7d0a92ef"
down_revision: str | None = "97ffb93a8ecb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = "ck_instances_gpu_count_nonneg"


def upgrade() -> None:
    op.execute(f"ALTER TABLE instances ADD CONSTRAINT {_NAME} CHECK (gpu_count >= 0) NOT VALID")
    op.execute(f"ALTER TABLE instances VALIDATE CONSTRAINT {_NAME}")


def downgrade() -> None:
    op.execute(f"ALTER TABLE instances DROP CONSTRAINT IF EXISTS {_NAME}")
