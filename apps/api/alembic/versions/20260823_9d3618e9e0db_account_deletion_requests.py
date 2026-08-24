"""account_deletion_requests:账号注销(F4)

- 新表 account_deletion_requests:pending → completed(执行匿名化)/ rejected(驳回,
  含执行前校验不过的自动驳回)/ cancelled(冷静期内用户撤销)。
- 部分唯一索引 uq_account_deletion_requests_pending_user(user_id WHERE status='pending'):
  每用户至多一条进行中申请,重复提交返回既有;终态不占位,可再申请。
- users.phone 加宽 20 → 40:注销匿名化把手机号改写为
  del:{user_id}:{原手机号 sha256 前 12 位}(最长 27 字符),释放原号码占用的唯一约束。

Revision ID: 9d3618e9e0db
Revises: 53c207e5ac95
Create Date: 2026-08-23 17:36:56.425996

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "9d3618e9e0db"
down_revision: str | None = "53c207e5ac95"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_deletion_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=256), nullable=False),
        sa.Column(
            "requested_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("processed_by", sa.Integer(), nullable=True),
        sa.Column("processed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("note", sa.String(length=512), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account_deletion_requests")),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'completed', 'rejected', 'cancelled')",
            name=op.f("ck_account_deletion_requests_status"),
        ),
    )
    op.create_index(
        op.f("ix_account_deletion_requests_user_id"), "account_deletion_requests", ["user_id"]
    )
    op.create_index(
        op.f("ix_account_deletion_requests_status"), "account_deletion_requests", ["status"]
    )
    op.create_index(
        "uq_account_deletion_requests_pending_user",
        "account_deletion_requests",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.alter_column("users", "phone", type_=sa.String(length=40))


def downgrade() -> None:
    op.alter_column("users", "phone", type_=sa.String(length=20))
    op.drop_index(
        "uq_account_deletion_requests_pending_user", table_name="account_deletion_requests"
    )
    op.drop_index(
        op.f("ix_account_deletion_requests_status"), table_name="account_deletion_requests"
    )
    op.drop_index(
        op.f("ix_account_deletion_requests_user_id"), table_name="account_deletion_requests"
    )
    op.drop_table("account_deletion_requests")
