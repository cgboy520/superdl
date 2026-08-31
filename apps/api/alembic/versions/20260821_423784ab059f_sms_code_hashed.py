"""sms code hashed

Revision ID: 423784ab059f
Revises: aa76ffcba115
Create Date: 2026-08-21 22:19:45.968802

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "423784ab059f"
down_revision: str | None = "aa76ffcba115"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """验证码明文改带密钥摘要(HMAC-SHA256,见 core.crypto.hash_sms_code)。

    不做数据迁移,直接清表:活跃验证码的 TTL 只有几分钟,最坏影响是极少数用户要重发一次,
    而保留明文列做灰度窗口没有任何收益 —— 这条改动的全部意义就是让库里不再有明文。
    """
    op.add_column("sms_codes", sa.Column("code_hash", sa.String(length=64), nullable=True))
    op.execute("DELETE FROM sms_codes")
    # 清表后全表校验零行零成本,SET NOT NULL 无实际扫描(门禁按形式拦,此处标注理由)
    op.alter_column(  # ddl-risk: reviewed —— 上一行已 DELETE 全表,校验零行
        "sms_codes", "code_hash", nullable=False
    )
    op.drop_column(  # ddl-risk: reviewed —— 本迁移的语义就是摘除明文列(瞬时 catalog 锁)
        "sms_codes", "code"
    )


def downgrade() -> None:
    op.add_column("sms_codes", sa.Column("code", sa.VARCHAR(length=8), nullable=True))
    op.execute("DELETE FROM sms_codes")
    op.alter_column("sms_codes", "code", nullable=False)
    op.drop_column("sms_codes", "code_hash")
