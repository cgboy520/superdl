"""service container: workload_type + service_endpoints + service_api_keys

服务型实例(workload_type='service')的落库形态:实例表加四列,新增端点与 API Key 两张表。
全部 expand-only —— 只加列加表,不动任何既有列的类型/可空性/默认值。

Revision ID: f3ae31c4d632
Revises: c4e1f70a2b93
Create Date: 2026-08-27 19:50:58.351868

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f3ae31c4d632"
down_revision: str | None = "c4e1f70a2b93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "service_endpoints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("instance_id", sa.Integer(), nullable=False),
        sa.Column("public_slug", sa.String(length=32), nullable=False),
        sa.Column("container_port", sa.Integer(), nullable=False),
        sa.Column("protocol", sa.String(length=8), server_default="http", nullable=False),
        sa.Column("health_path", sa.String(length=128), nullable=True),
        sa.Column("require_api_key", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "container_port BETWEEN 1 AND 65535 AND container_port NOT IN (22, 8888)",
            name=op.f("ck_service_endpoints_container_port"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service_endpoints")),
        # 一实例一端点(第一版):多端点会把「端点 ↔ Key 归属」变成多对多
        sa.UniqueConstraint("instance_id", name=op.f("uq_service_endpoints_instance_id")),
        # 公网域名左标签,必须全局唯一 —— 网关侧是按 Host 反查 slug 定位实例的
        sa.UniqueConstraint("public_slug", name=op.f("uq_service_endpoints_public_slug")),
    )
    op.create_table(
        "service_api_keys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("instance_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service_api_keys")),
        # 唯一索引即鉴权回源的查询路径:网关每个请求都要按 key_hash 查一行,
        # 这条约束顺带把它变成主键级单行查(extAuth 结果不可缓存,回源量等于业务 QPS)
        sa.UniqueConstraint("key_hash", name=op.f("uq_service_api_keys_key_hash")),
    )
    op.create_index(
        op.f("ix_service_api_keys_instance_id"), "service_api_keys", ["instance_id"], unique=False
    )
    op.create_index(
        op.f("ix_service_api_keys_user_id"), "service_api_keys", ["user_id"], unique=False
    )

    op.add_column(
        "instances",
        sa.Column("workload_type", sa.String(length=8), server_default="dev", nullable=False),
    )
    op.add_column(
        "instances",
        sa.Column("container_command", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "instances",
        sa.Column("container_args", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("instances", sa.Column("env_encrypted", sa.Text(), nullable=True))
    op.add_column(
        "instances",
        sa.Column("with_ssh", sa.Boolean(), server_default="true", nullable=False),
    )

    # instances 是热表(每台实例一行,状态机每次迁移都写)。ADD CONSTRAINT 默认要全表扫描
    # 校验存量行,期间持 ACCESS EXCLUSIVE —— 开机/关机/结算全阻塞在这把锁上。
    # NOT VALID 只对新写入生效、瞬时完成;VALIDATE 单独一步,只持 SHARE UPDATE EXCLUSIVE,
    # 与读写并发。存量行有 server_default='dev' 天然满足,VALIDATE 不会失败。
    op.execute(
        "ALTER TABLE instances ADD CONSTRAINT ck_instances_workload_type"
        " CHECK (workload_type IN ('dev', 'service')) NOT VALID"
    )
    op.execute("ALTER TABLE instances VALIDATE CONSTRAINT ck_instances_workload_type")


def downgrade() -> None:
    op.execute("ALTER TABLE instances DROP CONSTRAINT ck_instances_workload_type")
    op.drop_column("instances", "with_ssh")
    op.drop_column("instances", "env_encrypted")
    op.drop_column("instances", "container_args")
    op.drop_column("instances", "container_command")
    op.drop_column("instances", "workload_type")
    op.drop_index(op.f("ix_service_api_keys_user_id"), table_name="service_api_keys")
    op.drop_index(op.f("ix_service_api_keys_instance_id"), table_name="service_api_keys")
    op.drop_table("service_api_keys")
    op.drop_table("service_endpoints")
