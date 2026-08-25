"""admin_totp_last_timestep

TOTP 防重放(RFC 6238 §5.2):验证方对同一 timestep 只接受一次 OTP。
此前 verify(valid_window=1) 通过后不记录已用步,同一动态码在最长约 90s 窗口内
可重复通过、每次都签发新 access token——截获一枚码即可窗口内批量领管理端会话,
MFA 形同虚设。本列记录该账号已通过验证的最大 timestep,行锁内单调推进,
≤ 已用步的码一律拒绝(恢复码不受此限:自身一次性作废语义已覆盖)。

Revision ID: b8d3e5f7a9c2
Revises: a7c8e2f4b6d1
Create Date: 2026-08-25 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "b8d3e5f7a9c2"
down_revision: str | None = "a7c8e2f4b6d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("admin_users", sa.Column("last_totp_timestep", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("admin_users", "last_totp_timestep")
