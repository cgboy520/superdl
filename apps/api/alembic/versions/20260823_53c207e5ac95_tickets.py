"""tickets:工单系统(F3)

- 新表 tickets:open → pending_staff(用户回复)→ pending_user(客服回复)
  → resolved(任一方标记)→ closed(仅 resolved 后可;closed_at 仅此落)。
- 新表 ticket_messages:对话流,FK → tickets.id。
- (user_id, idempotency_key) 唯一:创建幂等,重放返回既有单(NULL 互不冲突)。

Revision ID: 53c207e5ac95
Revises: eff39853c59b
Create Date: 2026-08-23 16:33:06.533537

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "53c207e5ac95"
down_revision: str | None = "eff39853c59b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tickets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticket_no", sa.String(length=20), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("subject", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("instance_uuid", sa.String(length=32), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("closed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tickets")),
        sa.UniqueConstraint("ticket_no", name=op.f("uq_tickets_ticket_no")),
        sa.UniqueConstraint(
            "user_id", "idempotency_key", name=op.f("uq_tickets_user_id_idempotency_key")
        ),
        sa.CheckConstraint(
            "category IN ('instance', 'billing', 'data', 'account', 'other')",
            name=op.f("ck_tickets_category"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'pending_staff', 'pending_user', 'resolved', 'closed')",
            name=op.f("ck_tickets_status"),
        ),
    )
    op.create_index(op.f("ix_tickets_user_id"), "tickets", ["user_id"])
    op.create_index(op.f("ix_tickets_status"), "tickets", ["status"])
    op.create_table(
        "ticket_messages",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("sender_kind", sa.String(length=8), nullable=False),
        sa.Column("sender_id", sa.Integer(), nullable=False),
        sa.Column("body", sa.String(length=4000), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["tickets.id"],
            name=op.f("fk_ticket_messages_ticket_id_tickets"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ticket_messages")),
        sa.CheckConstraint(
            "sender_kind IN ('user', 'staff')",
            name=op.f("ck_ticket_messages_sender_kind"),
        ),
    )
    op.create_index(op.f("ix_ticket_messages_ticket_id"), "ticket_messages", ["ticket_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_ticket_messages_ticket_id"), table_name="ticket_messages")
    op.drop_table("ticket_messages")
    op.drop_index(op.f("ix_tickets_status"), table_name="tickets")
    op.drop_index(op.f("ix_tickets_user_id"), table_name="tickets")
    op.drop_table("tickets")
