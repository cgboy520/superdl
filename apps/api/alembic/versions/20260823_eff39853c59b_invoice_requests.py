"""invoice_requests:发票闭环(F2)

- 新表 invoice_requests:submitted → issued / rejected。按账期合并开具,一个自然月一张;
  amount 由服务端按账期计算(Σ paid 充值 − Σ submitted+issued 申请),客户端不可指定。
- 部分唯一索引 uq_invoice_requests_active_period(user_id, period WHERE submitted/issued)
  防重复申请;rejected 不占位,用户可修改抬头后重新申请。
- (user_id, idempotency_key) 唯一:创建幂等,重放返回既有单(NULL 互不冲突)。

Revision ID: eff39853c59b
Revises: 38fe91299a8b
Create Date: 2026-08-23 15:11:59.832708

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "eff39853c59b"
down_revision: str | None = "38fe91299a8b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invoice_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("title_type", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("tax_id", sa.String(length=32), nullable=True),
        sa.Column("email", sa.String(length=128), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("invoice_no", sa.String(length=64), nullable=True),
        sa.Column("reject_reason", sa.String(length=256), nullable=True),
        sa.Column("issued_by", sa.Integer(), nullable=True),
        sa.Column("issued_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoice_requests")),
        sa.UniqueConstraint(
            "user_id",
            "idempotency_key",
            name=op.f("uq_invoice_requests_user_id_idempotency_key"),
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_invoice_requests_amount_positive")),
        sa.CheckConstraint(
            "status IN ('submitted', 'issued', 'rejected')",
            name=op.f("ck_invoice_requests_status"),
        ),
        sa.CheckConstraint(
            "title_type IN ('personal', 'company')",
            name=op.f("ck_invoice_requests_title_type"),
        ),
    )
    op.create_index(op.f("ix_invoice_requests_user_id"), "invoice_requests", ["user_id"])
    op.create_index(op.f("ix_invoice_requests_status"), "invoice_requests", ["status"])
    op.create_index(
        "uq_invoice_requests_active_period",
        "invoice_requests",
        ["user_id", "period"],
        unique=True,
        postgresql_where=sa.text("status IN ('submitted', 'issued')"),
    )


def downgrade() -> None:
    op.drop_index("uq_invoice_requests_active_period", table_name="invoice_requests")
    op.drop_index(op.f("ix_invoice_requests_status"), table_name="invoice_requests")
    op.drop_index(op.f("ix_invoice_requests_user_id"), table_name="invoice_requests")
    op.drop_table("invoice_requests")
