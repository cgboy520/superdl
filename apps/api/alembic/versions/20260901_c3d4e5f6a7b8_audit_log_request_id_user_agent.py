"""audit_log.request_id / user_agent:审计行与日志之间的连接键

ObservabilityMiddleware 给每次请求绑一个 request_id(结构化日志的每一行都带它,
响应头 X-Request-ID 也回带),但审计行里没有,排查时无法从一条审计反查那次请求的日志。
user_agent 一并落:同一 actor 的异常客户端是最先能看出来的信号。

expand-only:两列都可空、无默认,存量行留 NULL(历史请求本就取不到)。

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-01 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("audit_log", sa.Column("request_id", sa.String(length=64), nullable=True))
    op.add_column("audit_log", sa.Column("user_agent", sa.String(length=256), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_log", "user_agent")
    op.drop_column("audit_log", "request_id")
