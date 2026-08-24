"""refund_requests:退款闭环(F1)

- 新表 refund_requests:pending → approved/rejected → paid / cancelled。
  审批通过 ≠ 出金,登记打款成功才同事务钱包负向调账(balance_ledger type='refund')。
- 部分唯一索引 uq_refund_requests_active_order(order_no WHERE 活跃态)防重复申请;
  rejected/cancelled 不占位,用户可重新申请。
- 双人制衡:CHECK payout_not_reviewer 兜底 payout_by <> review_by(应用层先拦 409)。
- (user_id, idempotency_key) 唯一:创建幂等,重放返回既有单(NULL 互不冲突)。
- balance_ledger.ref_type CHECK 扩容 + 'refund_request'(退款核销流水的 ref_type)。
  扩枚举按既有惯例 NOT VALID 新约束 + VALIDATE,再删旧约束。

Revision ID: 38fe91299a8b
Revises: a963843c4898
Create Date: 2026-08-23 14:20:28.154863

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "38fe91299a8b"
down_revision: str | None = "a963843c4898"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_REF_TYPES = "('bill_hourly', 'bill_daily_disk', 'order', 'adjustment')"
_NEW_REF_TYPES = "('bill_hourly', 'bill_daily_disk', 'order', 'adjustment', 'refund_request')"


def upgrade() -> None:
    op.create_table(
        "refund_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("refund_no", sa.String(length=20), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("order_no", sa.String(length=40), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("reason", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("review_by", sa.Integer(), nullable=True),
        sa.Column("review_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("review_comment", sa.String(length=256), nullable=True),
        sa.Column("payout_channel", sa.String(length=32), nullable=True),
        sa.Column("payout_ref", sa.String(length=128), nullable=True),
        sa.Column("payout_by", sa.Integer(), nullable=True),
        sa.Column("payout_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("wallet_entry_id", sa.BigInteger(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refund_requests")),
        sa.UniqueConstraint("refund_no", name=op.f("uq_refund_requests_refund_no")),
        sa.UniqueConstraint(
            "user_id",
            "idempotency_key",
            name=op.f("uq_refund_requests_user_id_idempotency_key"),
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_refund_requests_amount_positive")),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'paid', 'cancelled')",
            name=op.f("ck_refund_requests_status"),
        ),
        sa.CheckConstraint(
            "payout_by IS NULL OR review_by IS NULL OR payout_by <> review_by",
            name=op.f("ck_refund_requests_payout_not_reviewer"),
        ),
    )
    op.create_index(op.f("ix_refund_requests_user_id"), "refund_requests", ["user_id"])
    op.create_index(op.f("ix_refund_requests_status"), "refund_requests", ["status"])
    op.create_index(
        "uq_refund_requests_active_order",
        "refund_requests",
        ["order_no"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'approved', 'paid')"),
    )

    # balance_ledger.ref_type 枚举扩容:新约束 NOT VALID → VALIDATE → 删旧约束
    op.execute(
        "ALTER TABLE balance_ledger ADD CONSTRAINT ck_balance_ledger_ref_type_new"
        f" CHECK (ref_type IS NULL OR ref_type IN {_NEW_REF_TYPES}) NOT VALID"
    )
    op.execute("ALTER TABLE balance_ledger VALIDATE CONSTRAINT ck_balance_ledger_ref_type_new")
    op.execute("ALTER TABLE balance_ledger DROP CONSTRAINT ck_balance_ledger_ref_type")
    op.execute(
        "ALTER TABLE balance_ledger RENAME CONSTRAINT"
        " ck_balance_ledger_ref_type_new TO ck_balance_ledger_ref_type"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE balance_ledger ADD CONSTRAINT ck_balance_ledger_ref_type_old"
        f" CHECK (ref_type IS NULL OR ref_type IN {_OLD_REF_TYPES}) NOT VALID"
    )
    op.execute("ALTER TABLE balance_ledger VALIDATE CONSTRAINT ck_balance_ledger_ref_type_old")
    op.execute("ALTER TABLE balance_ledger DROP CONSTRAINT ck_balance_ledger_ref_type")
    op.execute(
        "ALTER TABLE balance_ledger RENAME CONSTRAINT"
        " ck_balance_ledger_ref_type_old TO ck_balance_ledger_ref_type"
    )

    op.drop_index("uq_refund_requests_active_order", table_name="refund_requests")
    op.drop_index(op.f("ix_refund_requests_status"), table_name="refund_requests")
    op.drop_index(op.f("ix_refund_requests_user_id"), table_name="refund_requests")
    op.drop_table("refund_requests")
