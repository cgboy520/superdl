"""users 增加可空的 id_number_hmac 及索引;五张表增加 (归属列, id) 复合索引。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6d2f8a1c0b77"
down_revision: str | None = "3b7e1c9a4f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("id_number_hmac", sa.String(length=64), nullable=True))
    op.create_index("ix_users_id_number_hmac", "users", ["id_number_hmac"])
    op.create_index("ix_instances_user_id_id", "instances", ["user_id", "id"])
    op.create_index("ix_instance_events_instance_id_id", "instance_events", ["instance_id", "id"])
    op.create_index("ix_bills_hourly_user_id_id", "bills_hourly", ["user_id", "id"])
    op.create_index("ix_balance_ledger_user_id_id", "balance_ledger", ["user_id", "id"])
    op.create_index("ix_services_user_id_id", "services", ["user_id", "id"])


def downgrade() -> None:
    raise RuntimeError("停机发布模型不支持回滚(fix-forward)")
