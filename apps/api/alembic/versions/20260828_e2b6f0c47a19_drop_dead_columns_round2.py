"""drop_dead_columns_round2

删掉代码里已无任何读写的列(模型、schema、前端与文档同提交移除):

- orders.type(恒 'recharge',没有第二种取值也没有读取方)
- skus.oversell_vram(纯定价参数,只被管理端表单读写,不进任何计算)
- skus.updated_at(近似库存缓存签名改口径后无读取方)
- usage_hourly.gpu_util_p95 / vram_max_mb / cpu_avg_pct(聚合只留 gpu_util_avg)
- service_endpoints.protocol(恒 'http',TCP/gRPC 未落地)

并把 account_deletion_requests 的 status CHECK 收窄:去掉从未写入过的 'approved'
——注销无「批准待执行」中间态,冷静期满直接执行匿名化。

DROP COLUMN 在 PG 里只改 catalog、不重写表,锁窗为瞬时 ACCESS EXCLUSIVE。
downgrade 只恢复列形态与旧 CHECK:非空列的历史值不可恢复,按 server_default 回填。

Revision ID: e2b6f0c47a19
Revises: c8f2a41d7e35
Create Date: 2026-08-28 09:12:44.106238

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2b6f0c47a19"
down_revision: str | None = "c8f2a41d7e35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DELETION_STATUS_CK = "ck_account_deletion_requests_status"
_OLD_DELETION_STATUS = "status IN ('pending', 'approved', 'completed', 'rejected', 'cancelled')"
_NEW_DELETION_STATUS = "status IN ('pending', 'completed', 'rejected', 'cancelled')"

# 删列均属同一评审结论(contract 窗口:上一版应用早已不读写这些列,本提交之前全仓 grep
# 无引用,'approved' 从未被任何代码路径写入,且当前无生产库;锁窗为瞬时 ACCESS EXCLUSIVE)。
# 门禁要求逐行标注,理由统一在此。


def upgrade() -> None:
    op.drop_column("orders", "type")  # ddl-risk: reviewed —— contract 窗口(见文件头)
    op.drop_column("skus", "oversell_vram")  # ddl-risk: reviewed —— 同上
    op.drop_column("skus", "updated_at")  # ddl-risk: reviewed —— 同上
    op.drop_column("usage_hourly", "gpu_util_p95")  # ddl-risk: reviewed —— 同上
    op.drop_column("usage_hourly", "vram_max_mb")  # ddl-risk: reviewed —— 同上
    op.drop_column("usage_hourly", "cpu_avg_pct")  # ddl-risk: reviewed —— 同上
    op.drop_column("service_endpoints", "protocol")  # ddl-risk: reviewed —— 同上
    op.drop_constraint(op.f(_DELETION_STATUS_CK), "account_deletion_requests", type_="check")
    # 收窄 CHECK:NOT VALID 先行(不锁表扫存量;'approved' 从未被写入,VALIDATE 必过),
    # 再独立 VALIDATE(只持 SHARE UPDATE EXCLUSIVE)
    op.execute(
        f"ALTER TABLE account_deletion_requests ADD CONSTRAINT {_DELETION_STATUS_CK} "
        f"CHECK ({_NEW_DELETION_STATUS}) NOT VALID"
    )
    op.execute(f"ALTER TABLE account_deletion_requests VALIDATE CONSTRAINT {_DELETION_STATUS_CK}")


def downgrade() -> None:
    op.drop_constraint(op.f(_DELETION_STATUS_CK), "account_deletion_requests", type_="check")
    op.create_check_constraint(
        op.f(_DELETION_STATUS_CK), "account_deletion_requests", _OLD_DELETION_STATUS
    )
    op.add_column(
        "service_endpoints",
        sa.Column(
            "protocol", sa.VARCHAR(length=8), server_default=sa.text("'http'"), nullable=False
        ),
    )
    op.add_column("usage_hourly", sa.Column("cpu_avg_pct", sa.Float(), nullable=True))
    op.add_column("usage_hourly", sa.Column("vram_max_mb", sa.Integer(), nullable=True))
    op.add_column("usage_hourly", sa.Column("gpu_util_p95", sa.Float(), nullable=True))
    op.add_column(
        "skus",
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.add_column(
        "skus",
        sa.Column(
            "oversell_vram",
            sa.NUMERIC(precision=4, scale=2),
            server_default=sa.text("1.00"),
            nullable=False,
        ),
    )
    op.add_column(
        "orders",
        sa.Column(
            "type", sa.VARCHAR(length=16), server_default=sa.text("'recharge'"), nullable=False
        ),
    )
