"""cluster_status.ingress_ready → gateway_ready(北向入口迁 Gateway API)

ingress-nginx 2026-03 退休,北向入口整体换成 Gateway API + Envoy Gateway,
探测口径随之变了:原来看的是「ingress-nginx-controller Deployment ready≥1」,
现在看的是「Gateway 对象 status.conditions 的 Programmed=True」——
后者才覆盖 listener 证书缺失 / hostname 撞车这类「控制器活着但流量进不来」的情形。

改名而不是加新列:cluster_status 是单行(id=1)派生缓存,巡检每轮整行覆写,
表里没有任何需要保住的历史。留一列名实不符的 ingress_ready 只会让下一个
读这张表的人以为平台还跑着 ingress-nginx。

Revision ID: c4e1f70a2b93
Revises: 5b1c7d0a92ef
Create Date: 2026-08-27 17:52:04.118207

"""

from collections.abc import Sequence

from alembic import op

revision: str = "c4e1f70a2b93"
down_revision: str | None = "5b1c7d0a92ef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 评审结论:cluster_status 是单行派生缓存(nodes 巡检每轮整行覆写,陈旧超 10min 即按
    # 「未知」处理),改名不丢任何事实,且下一轮巡检(≤5min)自动把新列填成真值。
    # 改名是纯 catalog 操作,不重写表、不扫行。
    op.alter_column(  # ddl-risk: reviewed —— 单行派生缓存改名,零事实损失
        "cluster_status", "ingress_ready", new_column_name="gateway_ready"
    )


def downgrade() -> None:
    op.alter_column("cluster_status", "gateway_ready", new_column_name="ingress_ready")
