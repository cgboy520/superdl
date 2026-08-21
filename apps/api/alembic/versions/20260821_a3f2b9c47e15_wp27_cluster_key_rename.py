"""wp27_cluster_key_rename

Revision ID: a3f2b9c47e15
Revises: d00e1b8449b6
Create Date: 2026-08-21 13:05:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "a3f2b9c47e15"
down_revision: str | None = "d00e1b8449b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 明文键直改;rke2_join_token 行不动——AES-GCM AAD=行 key,改键名会静默毁掉密文,
# 读侧走 LEGACY_KEY_ALIASES 回落,管理端下次保存时写新删旧(core/platform_config.py)。
RENAMES = {"rke2_server_url": "cluster_server_url", "rke2_version": "cluster_agent_version"}


def upgrade() -> None:
    for old, new in RENAMES.items():
        op.execute(
            "UPDATE platform_settings SET key = '{new}' WHERE key = '{old}' "
            "AND NOT EXISTS (SELECT 1 FROM platform_settings ps WHERE ps.key = '{new}')".format(
                new=new, old=old
            )
        )


def downgrade() -> None:
    for old, new in RENAMES.items():
        op.execute(
            "UPDATE platform_settings SET key = '{old}' WHERE key = '{new}' "
            "AND NOT EXISTS (SELECT 1 FROM platform_settings ps WHERE ps.key = '{old}')".format(
                new=new, old=old
            )
        )
