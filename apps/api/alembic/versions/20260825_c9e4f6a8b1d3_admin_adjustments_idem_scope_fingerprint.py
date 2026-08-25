"""admin_adjustments_idem_scope_fingerprint

调账幂等键加固(P2):
1. 作用域 (created_by, idempotency_key) → (created_by, user_id, idempotency_key):
   同一管理员对不同租户的两次调账若复用了同一键(如前端按日期生成的弱键),
   旧作用域会把第二次请求误判为重放、返回第一笔的调账单——跨租户错单。
2. 新增 request_fingerprint(请求体 SHA256):同键重放必须比对请求体,
   指纹不一致回 409(对齐 Stripe 幂等键惯例),弱键冲突从「静默错单」变「显式拒绝」。

Revision ID: c9e4f6a8b1d3
Revises: b8d3e5f7a9c2
Create Date: 2026-08-25 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "c9e4f6a8b1d3"
down_revision: str | None = "b8d3e5f7a9c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "admin_adjustments",
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
    )
    op.drop_constraint(
        "uq_admin_adjustments_created_by_idempotency_key", "admin_adjustments", type_="unique"
    )
    op.create_unique_constraint(
        "uq_admin_adjustments_idem_scope",
        "admin_adjustments",
        ["created_by", "user_id", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_admin_adjustments_idem_scope", "admin_adjustments", type_="unique")
    op.create_unique_constraint(
        "uq_admin_adjustments_created_by_idempotency_key",
        "admin_adjustments",
        ["created_by", "idempotency_key"],
    )
    op.drop_column("admin_adjustments", "request_fingerprint")
