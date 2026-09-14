"""策略参数并入平台配置中心:policy_overrides 行搬进 platform_settings 后删表。
数据影响:policy_overrides 表消失;已有覆盖值原样保留在 platform_settings
(同键已存在时以 platform_settings 为准)。

Revision ID: 8f1c3d5e2a46
Revises: 7e4a2b9c1d05
Create Date: 2026-09-12 11:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "8f1c3d5e2a46"
down_revision: str | None = "7e4a2b9c1d05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO platform_settings (key, value, updated_by, updated_at) "
        "SELECT key, value, NULL, updated_at FROM policy_overrides "
        "ON CONFLICT (key) DO NOTHING"
    )
    op.drop_table("policy_overrides")


def downgrade() -> None:
    raise RuntimeError("停机发布模型不支持回滚(fix-forward)")
