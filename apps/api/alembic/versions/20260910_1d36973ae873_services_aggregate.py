"""在线服务聚合根:services 表、instances 服务快照列、密钥归属改服务、删 service_endpoints。

数据影响:service_endpoints 每行升格为一条 services(含已释放实例的端点,作已删除服务保留,
账单与密钥归属可查);instances 的 service_* 快照列从端点行回填;service_api_keys.instance_id
改为 service_id 后删列;service_endpoints 整表删除。create_instance 的幂等指纹因参数集变化
而变形,发布前 24h 内带 Idempotency-Key 建过实例的重试会 409(同键异参)。

Revision ID: 1d36973ae873
Revises: 1620c05976ce
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1d36973ae873"
down_revision: str | None = "1620c05976ce"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "services",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("public_slug", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("protocol", sa.String(length=8), server_default="http", nullable=False),
        sa.Column("require_api_key", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("desired_state", sa.String(length=8), server_default="running", nullable=False),
        sa.Column("current_instance_id", sa.Integer(), nullable=True),
        sa.Column("rollout_instance_id", sa.Integer(), nullable=True),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("released_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("protocol IN ('http')", name=op.f("ck_services_protocol")),
        sa.CheckConstraint(
            "desired_state IN ('running', 'stopped')", name=op.f("ck_services_desired_state")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_services")),
        sa.UniqueConstraint("public_slug", name=op.f("uq_services_public_slug")),
    )
    op.create_index(op.f("ix_services_user_id"), "services", ["user_id"], unique=False)

    op.add_column("instances", sa.Column("service_id", sa.Integer(), nullable=True))
    op.add_column("instances", sa.Column("service_revision", sa.Integer(), nullable=True))
    op.add_column("instances", sa.Column("service_slug", sa.String(length=32), nullable=True))
    op.add_column("instances", sa.Column("service_port", sa.Integer(), nullable=True))
    op.add_column("instances", sa.Column("health_path", sa.String(length=128), nullable=True))
    op.create_index(op.f("ix_instances_service_id"), "instances", ["service_id"], unique=False)
    op.add_column("service_api_keys", sa.Column("service_id", sa.Integer(), nullable=True))

    # 数据回填:每个端点升格为一条服务(实例已释放的也建,作已删除服务保留归属)
    op.execute(
        sa.text(
            """
            INSERT INTO services (public_slug, user_id, name, protocol, require_api_key,
                                  desired_state, current_instance_id, revision,
                                  created_at, updated_at, released_at)
            SELECT e.public_slug, i.user_id, i.name, 'http', e.require_api_key,
                   CASE WHEN i.status IN ('creating', 'starting', 'running')
                        THEN 'running' ELSE 'stopped' END,
                   i.id, 1, e.created_at, now(),
                   CASE WHEN i.status = 'released' THEN COALESCE(
                       (SELECT max(ev.created_at) FROM instance_events ev
                         WHERE ev.instance_id = i.id AND ev.to_status = 'released'),
                       i.updated_at) END
              FROM service_endpoints e
              JOIN instances i ON i.id = e.instance_id
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE instances i
               SET service_id = s.id, service_revision = 1, service_slug = e.public_slug,
                   service_port = e.container_port, health_path = e.health_path
              FROM service_endpoints e
              JOIN services s ON s.public_slug = e.public_slug
             WHERE i.id = e.instance_id
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE service_api_keys k SET service_id = i.service_id
              FROM instances i WHERE i.id = k.instance_id
            """
        )
    )
    # 找不到归属服务的密钥行(端点行缺失的孤儿)随端点表一起消失
    op.execute(sa.text("DELETE FROM service_api_keys WHERE service_id IS NULL"))

    op.alter_column("service_api_keys", "service_id", nullable=False)
    op.create_index(
        op.f("ix_service_api_keys_service_id"), "service_api_keys", ["service_id"], unique=False
    )
    op.drop_index("ix_service_api_keys_instance_id", table_name="service_api_keys")
    op.drop_column("service_api_keys", "instance_id")

    op.create_check_constraint(
        op.f("ck_instances_service_shape"),
        "instances",
        "(workload_type = 'service') = (service_id IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_instances_service_port"),
        "instances",
        "service_port IS NULL OR (service_port BETWEEN 1 AND 65535"
        " AND service_port NOT IN (22, 8888))",
    )
    op.drop_table("service_endpoints")


def downgrade() -> None:
    raise RuntimeError("停机发布模型不支持回滚(fix-forward);见 docs/decisions.md")
