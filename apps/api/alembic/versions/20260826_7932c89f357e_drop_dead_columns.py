"""drop_dead_columns

删掉代码里已无任何读写的列(模型、schema 与业务代码同提交移除):

- wallets.frozen_amount(及 CHECK ck_wallets_frozen_nonneg)
- orders.invoice_title / invoice_tax_id
- users.company_name / company_tax_id / invoice_title
- used_refresh_tokens.user_id
- instances.pod_name
- node_specs.desired_at
- cluster_status.detail

DROP COLUMN 在 PG 里只改 catalog、不重写表,锁窗为瞬时 ACCESS EXCLUSIVE。
downgrade 只恢复列形态:两列 NOT NULL 的历史值不可恢复,frozen_amount 按默认 0 回填,
used_refresh_tokens.user_id 恢复为可空。

Revision ID: 7932c89f357e
Revises: d0f5a7b9c2e4
Create Date: 2026-08-26 00:47:22.874324

"""

# ddl-risk: reviewed —— 删列属 contract 窗口:上一版应用早已不读写这些列(本提交之前
# 全仓 grep 无引用),且当前无生产库,无需 expand/contract 两窗口分发。

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7932c89f357e"
down_revision: str | None = "d0f5a7b9c2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("cluster_status", "detail")
    op.drop_column("instances", "pod_name")
    op.drop_column("node_specs", "desired_at")
    op.drop_column("orders", "invoice_title")
    op.drop_column("orders", "invoice_tax_id")
    op.drop_column("used_refresh_tokens", "user_id")
    op.drop_column("users", "company_name")
    op.drop_column("users", "company_tax_id")
    op.drop_column("users", "invoice_title")
    op.drop_constraint(op.f("ck_wallets_frozen_nonneg"), "wallets", type_="check")
    op.drop_column("wallets", "frozen_amount")


def downgrade() -> None:
    op.add_column(
        "wallets",
        sa.Column(
            "frozen_amount",
            sa.NUMERIC(precision=14, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_wallets_frozen_nonneg"), "wallets", "frozen_amount >= 0::numeric"
    )
    op.add_column("users", sa.Column("invoice_title", sa.VARCHAR(length=128), nullable=True))
    op.add_column("users", sa.Column("company_tax_id", sa.VARCHAR(length=32), nullable=True))
    op.add_column("users", sa.Column("company_name", sa.VARCHAR(length=128), nullable=True))
    op.add_column("used_refresh_tokens", sa.Column("user_id", sa.INTEGER(), nullable=True))
    op.add_column("orders", sa.Column("invoice_tax_id", sa.VARCHAR(length=32), nullable=True))
    op.add_column("orders", sa.Column("invoice_title", sa.VARCHAR(length=128), nullable=True))
    op.add_column(
        "node_specs",
        sa.Column("desired_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column("instances", sa.Column("pod_name", sa.VARCHAR(length=64), nullable=True))
    op.add_column(
        "cluster_status",
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
