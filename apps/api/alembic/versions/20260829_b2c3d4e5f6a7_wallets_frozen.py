"""wallets.frozen:渠道冲正冻结额

已入账订单收到渠道关单/退款通知(channel_reversed_at)时,按订单额等额冻结钱包:
冻结额不可用余额(balance - frozen)仍可被 assert_can_afford / 退款上限看到,
但不再被新消费吃掉——冲正到人工核销之间,余额不得继续流出。
核销走管理端 /finance/reversals/{order_no}/resolve:release(解冻,渠道噪音单)
或 chargeback(解冻 + 等额扣减,钱确实被渠道拿回)。

expand-only:新增列带 server_default 0,存量行无感。

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-29 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "wallets",
        sa.Column("frozen", sa.Numeric(14, 2), server_default="0", nullable=False),
    )
    op.create_check_constraint("frozen_nonneg", "wallets", "frozen >= 0")


def downgrade() -> None:
    op.drop_constraint("frozen_nonneg", "wallets", type_="check")
    op.drop_column("wallets", "frozen")
