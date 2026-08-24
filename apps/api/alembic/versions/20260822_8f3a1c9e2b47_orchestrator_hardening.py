"""orchestrator hardening:列宽/索引/CHECK 兜底/fillfactor

Revision ID: 8f3a1c9e2b47
Revises: c1f7a92e5b38
Create Date: 2026-08-22 19:00:00.000000

编排器批次审计修复的数据层部分:

- instances.node_name 放宽到 253(K8s 节点名上限,与 node_specs 同宽;原 64 会在长
  主机名集群写入失败);jupyter_token 放宽到 160(改存 AES-GCM 密文,密文约 90 字符)。
  两个 varchar 扩容在 PG 里都是 metadata-only,不重写表。
- 部分唯一索引:port_allocations(instance_id)、data_disks(mounted_instance_id)
  —— 一台实例至多占一个端口/挂一块盘,与 1:1 模型一致;NULL(空闲)不参与约束。
- 复合/部分索引:outbox 领取 (status, next_retry_at, id);users 非 active 部分索引
  (active 是大头,不入索引);audit_log (actor_id, id);bills_hourly (user_id,
  hour_start);bills_daily_disk (user_id, day)。
  ix_bills_hourly_instance_id 保留:它声明在 billing 模型里(本批次无 ownership),
  前导列冗余的清理留给 billing 归属方。
- skus 业务唯一键 (gpu_model, tier, mig_profile, gpu_cores_pct),NULLS NOT DISTINCT
  (非 mig 档 mig_profile 为 NULL,普通唯一约束会把 NULL 当互不相等而放过重复)。
- CHECK 兜底(全部 NOT VALID 先行、同 revision 内独立 VALIDATE;存量脏数据不阻塞
  上线,校验通过前只挡新写入):
  balance_ledger.ref_type 枚举、admin_adjustments amount<>0 / status 枚举 /
  复核人不能是发起人、instances/orders/outbox_tasks status 枚举、cluster_status 单行。
- fillfactor:rate_limit_counters=80、outbox_tasks=85(HOT 更新友好,两类表都是
  高频原地更新)。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8f3a1c9e2b47"
down_revision: str | None = "c1f7a92e5b38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (约束名, 表, 谓词):NOT VALID 创建后统一 VALIDATE
_CHECKS: list[tuple[str, str, str]] = [
    (
        "ck_balance_ledger_ref_type",
        "balance_ledger",
        "ref_type IS NULL OR ref_type IN ('bill_hourly', 'bill_daily_disk', 'order', 'adjustment')",
    ),
    ("ck_admin_adjustments_amount_nonzero", "admin_adjustments", "amount <> 0"),
    (
        "ck_admin_adjustments_status",
        "admin_adjustments",
        "status IN ('pending', 'approved', 'rejected')",
    ),
    (
        "ck_admin_adjustments_reviewer_not_creator",
        "admin_adjustments",
        "reviewed_by IS NULL OR reviewed_by <> created_by",
    ),
    (
        "ck_instances_status",
        "instances",
        "status IN ('creating', 'running', 'stopping', 'stopped', 'starting', 'frozen',"
        " 'releasing', 'released', 'failed')",
    ),
    ("ck_orders_status", "orders", "status IN ('pending', 'paid', 'failed', 'closed')"),
    (
        "ck_outbox_tasks_status",
        "outbox_tasks",
        "status IN ('pending', 'running', 'done', 'dead', 'discarded')",
    ),
    ("ck_cluster_status_singleton", "cluster_status", "id = 1"),
]


def upgrade() -> None:
    # 列宽(metadata-only 扩容)
    op.alter_column(
        "instances",
        "node_name",
        existing_type=sa.String(length=64),
        type_=sa.String(length=253),
        existing_nullable=True,
    )
    op.alter_column(
        "instances",
        "jupyter_token",
        existing_type=sa.String(length=64),
        type_=sa.String(length=160),
        existing_nullable=False,
    )

    # 部分唯一索引(1:1 占用语义)
    op.create_index(
        "uq_port_allocations_instance",
        "port_allocations",
        ["instance_id"],
        unique=True,
        postgresql_where=sa.text("instance_id IS NOT NULL"),
    )
    op.create_index(
        "uq_data_disks_mounted_instance",
        "data_disks",
        ["mounted_instance_id"],
        unique=True,
        postgresql_where=sa.text("mounted_instance_id IS NOT NULL"),
    )

    # 查询路径复合/部分索引
    op.create_index(
        "ix_outbox_tasks_status_next_retry_at_id",
        "outbox_tasks",
        ["status", "next_retry_at", "id"],
    )
    op.create_index(
        "ix_users_status_not_active",
        "users",
        ["status"],
        postgresql_where=sa.text("status <> 'active'"),
    )
    op.create_index("ix_audit_log_actor_id_id", "audit_log", ["actor_id", "id"])
    op.create_index("ix_bills_hourly_user_hour", "bills_hourly", ["user_id", "hour_start"])
    op.create_index("ix_bills_daily_disk_user_day", "bills_daily_disk", ["user_id", "day"])

    # SKU 业务唯一键(mig_profile NULL 参与判重)。
    # ddl-risk: reviewed —— UNIQUE 约束无 NOT VALID 形态(PG 仅 CHECK/FK 支持);
    # skus 是管理端低频维护的小表(种子 4 行),建唯一索引的短时锁可接受。
    # 存量库可能已有重复行(管理端误操作/种子重复,见审计 #73):先按业务键去重——
    # 保留每组最小 id,instances.sku_id(无 FK,仅展示引用)改挂保留行,再删重复行。
    # PARTITION BY 把 NULL 归为同组,与 NULLS NOT DISTINCT 语义一致。
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY gpu_model, tier, mig_profile, gpu_cores_pct
                       ORDER BY id
                   ) AS rn,
                   min(id) OVER (
                       PARTITION BY gpu_model, tier, mig_profile, gpu_cores_pct
                   ) AS keep_id
            FROM skus
        )
        UPDATE instances i SET sku_id = r.keep_id
        FROM ranked r
        WHERE i.sku_id = r.id AND r.rn > 1
        """
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY gpu_model, tier, mig_profile, gpu_cores_pct
                       ORDER BY id
                   ) AS rn
            FROM skus
        )
        DELETE FROM skus s USING ranked r
        WHERE s.id = r.id AND r.rn > 1
        """
    )
    op.execute(
        "ALTER TABLE skus ADD CONSTRAINT uq_skus_business_key "
        "UNIQUE NULLS NOT DISTINCT (gpu_model, tier, mig_profile, gpu_cores_pct)"
    )

    # CHECK 兜底:NOT VALID 先行(不扫描存量),随后独立 VALIDATE
    for name, table, predicate in _CHECKS:
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({predicate}) NOT VALID")
    for name, table, _predicate in _CHECKS:
        op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")

    # HOT 更新友好的填充因子
    op.execute("ALTER TABLE rate_limit_counters SET (fillfactor=80)")
    op.execute("ALTER TABLE outbox_tasks SET (fillfactor=85)")


def downgrade() -> None:
    op.execute("ALTER TABLE outbox_tasks RESET (fillfactor)")
    op.execute("ALTER TABLE rate_limit_counters RESET (fillfactor)")
    for name, table, _predicate in reversed(_CHECKS):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
    op.execute("ALTER TABLE skus DROP CONSTRAINT uq_skus_business_key")
    op.drop_index("ix_bills_daily_disk_user_day", table_name="bills_daily_disk")
    op.drop_index("ix_bills_hourly_user_hour", table_name="bills_hourly")
    op.drop_index("ix_audit_log_actor_id_id", table_name="audit_log")
    op.drop_index("ix_users_status_not_active", table_name="users")
    op.drop_index("ix_outbox_tasks_status_next_retry_at_id", table_name="outbox_tasks")
    op.drop_index("uq_data_disks_mounted_instance", table_name="data_disks")
    op.drop_index("uq_port_allocations_instance", table_name="port_allocations")
    op.alter_column(
        "instances",
        "jupyter_token",
        existing_type=sa.String(length=160),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
    op.alter_column(
        "instances",
        "node_name",
        existing_type=sa.String(length=253),
        type_=sa.String(length=64),
        existing_nullable=True,
    )
