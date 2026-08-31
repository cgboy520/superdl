"""drop_dead_indexes

六个纯死索引(全仓零查询使用,逐一举证):
- ix_notifications_created_at:翻页/排序一律主键 id(notify/service.py)
- ix_balance_ledger_created_at:流水翻页/对账一律主键 id(wallet.py/export.py/reconcile.py)
- ix_node_enrollments_node_name:装机链路只按 token_hash/progress_token_hash 查行
- ix_node_specs_pool_label / ix_node_specs_gpu_model:台账只全量读后 Python 侧聚合
  (nodes/service.gpu_model_aggregates),无按列过滤
- ix_used_refresh_tokens_user_id:读取只按 jti 主键,清理按 expires_at

Revision ID: 4d6e8f0a2b4c
Revises: 16b6aefc6c67
Create Date: 2026-08-23 21:10:00.000000

"""

from collections.abc import Sequence

from alembic import op


revision: str = "4d6e8f0a2b4c"
down_revision: str | None = "16b6aefc6c67"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # DROP INDEX CONCURRENTLY:不阻塞表读写;CONCURRENTLY 不能在事务块内,
    # 必须 autocommit_block(env.py 整轮单事务)
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_notifications_created_at",
            table_name="notifications",
            postgresql_concurrently=True,
        )
        op.drop_index(
            "ix_balance_ledger_created_at",
            table_name="balance_ledger",
            postgresql_concurrently=True,
        )
        op.drop_index(
            "ix_node_enrollments_node_name",
            table_name="node_enrollments",
            postgresql_concurrently=True,
        )
        op.drop_index(
            "ix_node_specs_pool_label", table_name="node_specs", postgresql_concurrently=True
        )
        op.drop_index(
            "ix_node_specs_gpu_model", table_name="node_specs", postgresql_concurrently=True
        )
        op.drop_index(
            "ix_used_refresh_tokens_user_id",
            table_name="used_refresh_tokens",
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_used_refresh_tokens_user_id",
            "used_refresh_tokens",
            ["user_id"],
            postgresql_concurrently=True,
        )
        op.create_index(
            "ix_node_specs_gpu_model",
            "node_specs",
            ["gpu_model"],
            postgresql_concurrently=True,
        )
        op.create_index(
            "ix_node_specs_pool_label",
            "node_specs",
            ["pool_label"],
            postgresql_concurrently=True,
        )
        op.create_index(
            "ix_node_enrollments_node_name",
            "node_enrollments",
            ["node_name"],
            postgresql_concurrently=True,
        )
        op.create_index(
            "ix_balance_ledger_created_at",
            "balance_ledger",
            ["created_at"],
            postgresql_concurrently=True,
        )
        op.create_index(
            "ix_notifications_created_at",
            "notifications",
            ["created_at"],
            postgresql_concurrently=True,
        )
