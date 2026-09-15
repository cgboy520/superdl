"""将 policy_overrides 中 platform_settings 尚无的键值写入后者,随后删除 policy_overrides 表。"""

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
