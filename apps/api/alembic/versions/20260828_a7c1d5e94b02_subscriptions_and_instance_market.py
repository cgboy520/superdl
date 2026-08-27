"""subscriptions + instances.market + ledger ref_type 扩容

包周期(预付)落库形态:实例表加一列 market(怎么买),新增 subscriptions 一张表,
资金流水的 ref_type 白名单扩出 'subscription'。

expand-only 的边界说明:ref_type 的 CHECK 是**替换**(DROP + ADD),不是纯新增 ——
但它只放宽不收紧(旧值全部保留),存量行零违规,回滚也只是把新值再收回去。
这是评审过的枚举扩容,故标 ddl-risk。

Revision ID: a7c1d5e94b02
Revises: f3ae31c4d632
Create Date: 2026-08-28 09:12:04.118207

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c1d5e94b02"
down_revision: str | None = "f3ae31c4d632"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("instance_id", sa.Integer(), nullable=False),
        sa.Column("sku_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.String(length=8), nullable=False),
        sa.Column("period_count", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("amount_paid", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("auto_renew", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("renewed_from_id", sa.Integer(), nullable=True),
        sa.Column("warned_for_expiry", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount_paid >= 0", name=op.f("ck_subscriptions_amount_nonneg")),
        sa.CheckConstraint(
            "period IN ('day', 'week', 'month', 'year')", name=op.f("ck_subscriptions_period")
        ),
        sa.CheckConstraint(
            "period_count >= 1", name=op.f("ck_subscriptions_period_count_positive")
        ),
        sa.CheckConstraint(
            "status IN ('active', 'expired', 'cancelled')", name=op.f("ck_subscriptions_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscriptions")),
        sa.UniqueConstraint(
            "user_id", "idempotency_key", name=op.f("uq_subscriptions_user_id_idempotency_key")
        ),
    )
    op.create_index(
        "ix_subscriptions_active_expiry",
        "subscriptions",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        op.f("ix_subscriptions_expires_at"), "subscriptions", ["expires_at"], unique=False
    )
    op.create_index(
        op.f("ix_subscriptions_instance_id"), "subscriptions", ["instance_id"], unique=False
    )
    op.create_index(op.f("ix_subscriptions_status"), "subscriptions", ["status"], unique=False)
    op.create_index(op.f("ix_subscriptions_user_id"), "subscriptions", ["user_id"], unique=False)

    op.add_column(
        "skus", sa.Column("period_enabled", sa.Boolean(), server_default="true", nullable=False)
    )
    op.add_column(
        "instances",
        sa.Column("market", sa.String(length=16), server_default="on_demand", nullable=False),
    )
    # NOT VALID + 单独 VALIDATE:ADD CONSTRAINT 直接带校验会持 ACCESS EXCLUSIVE 全表扫,
    # 分两步的第二步只要 SHARE UPDATE EXCLUSIVE(不挡读写)。
    # 存量行由上面的 server_default 全部落成 'on_demand',VALIDATE 必过
    op.execute(
        "ALTER TABLE instances ADD CONSTRAINT ck_instances_market"
        " CHECK (market IN ('on_demand', 'spot', 'subscription')) NOT VALID"
    )
    op.execute("ALTER TABLE instances VALIDATE CONSTRAINT ck_instances_market")

    # ddl-risk: reviewed —— ref_type 白名单枚举扩容(只放宽不收紧)。DROP 旧 CHECK 是
    # 扩容的必经步骤,窗口内(DROP 与 ADD 之间)约束短暂缺失,但同一事务内完成,
    # 其它会话看不到中间态。新谓词是旧谓词的超集,存量行零违规
    op.execute("ALTER TABLE balance_ledger DROP CONSTRAINT ck_balance_ledger_ref_type")
    op.execute(
        "ALTER TABLE balance_ledger ADD CONSTRAINT ck_balance_ledger_ref_type"
        " CHECK (ref_type IS NULL OR ref_type IN"
        " ('bill_hourly', 'bill_daily_disk', 'order', 'adjustment', 'refund_request',"
        " 'subscription')) NOT VALID"
    )
    op.execute("ALTER TABLE balance_ledger VALIDATE CONSTRAINT ck_balance_ledger_ref_type")


def downgrade() -> None:
    op.execute("ALTER TABLE balance_ledger DROP CONSTRAINT IF EXISTS ck_balance_ledger_ref_type")
    op.execute(
        "ALTER TABLE balance_ledger ADD CONSTRAINT ck_balance_ledger_ref_type"
        " CHECK (ref_type IS NULL OR ref_type IN"
        " ('bill_hourly', 'bill_daily_disk', 'order', 'adjustment', 'refund_request')) NOT VALID"
    )
    op.execute("ALTER TABLE instances DROP CONSTRAINT IF EXISTS ck_instances_market")
    op.drop_column("instances", "market")
    op.drop_column("skus", "period_enabled")
    op.drop_index(op.f("ix_subscriptions_user_id"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_status"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_instance_id"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_expires_at"), table_name="subscriptions")
    op.drop_index(
        "ix_subscriptions_active_expiry",
        table_name="subscriptions",
        postgresql_where=sa.text("status = 'active'"),
    )
    op.drop_table("subscriptions")
