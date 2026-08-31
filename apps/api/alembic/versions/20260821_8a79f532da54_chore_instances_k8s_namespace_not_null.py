"""chore_instances_k8s_namespace_not_null

Revision ID: 8a79f532da54
Revises: a3f2b9c47e15
Create Date: 2026-08-21 14:25:10.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8a79f532da54"
down_revision: str | None = "a3f2b9c47e15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # create_instance 自始无条件写 k8s_namespace,列实际恒非空;补约束以移除下游层层判空。
    # 兜底回填仅覆盖理论上的历史脏行,写法与 create_instance 的前缀规则一致(默认 tenant-)。
    op.execute(
        "UPDATE instances SET k8s_namespace = 'tenant-' || user_id WHERE k8s_namespace IS NULL"
    )
    # SET NOT NULL 的三步法(避免对存量行全表校验的 ACCESS EXCLUSIVE 长锁):
    # CHECK ... NOT VALID 只约束新写(不扫存量)→ VALIDATE(只持 SHARE UPDATE EXCLUSIVE)
    # → SET NOT NULL 有 valid CHECK 佐证即跳过全表扫描(PG ≥12)
    op.execute(
        "ALTER TABLE instances ADD CONSTRAINT ck_instances_k8s_namespace_not_null "
        "CHECK (k8s_namespace IS NOT NULL) NOT VALID"
    )
    op.execute("ALTER TABLE instances VALIDATE CONSTRAINT ck_instances_k8s_namespace_not_null")
    # 有 valid CHECK 佐证,PG ≥12 跳过全表扫描(门禁按形式拦,此处标注三步法的最后一步)
    op.alter_column(  # ddl-risk: reviewed —— valid CHECK 佐证,SET NOT NULL 跳过全表扫描
        "instances", "k8s_namespace", existing_type=sa.String(length=64), nullable=False
    )
    op.execute("ALTER TABLE instances DROP CONSTRAINT ck_instances_k8s_namespace_not_null")


def downgrade() -> None:
    op.alter_column("instances", "k8s_namespace", existing_type=sa.String(length=64), nullable=True)
