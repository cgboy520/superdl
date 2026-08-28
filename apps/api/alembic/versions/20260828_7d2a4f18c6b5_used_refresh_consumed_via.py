"""used_refresh_tokens 记消费途径(登出重放收紧)

consumed_via: refresh(轮换)/ logout(登出)。登出消费的 jti 重放一律 401,
不再按宽限窗并发重试补发新对,也不 bump token_version(避免登出与并发首刷
竞态时误撤已合法轮换的在线会话)。老行 NULL,应用层按旧语义处理。

Revision ID: 7d2a4f18c6b5
Revises: 5e1c9b47a3d8
Create Date: 2026-08-28 18:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7d2a4f18c6b5"
down_revision: str | None = "5e1c9b47a3d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("used_refresh_tokens", sa.Column("consumed_via", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("used_refresh_tokens", "consumed_via")
