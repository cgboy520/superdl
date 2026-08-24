"""聚合全部 SQLAlchemy 模型,供 alembic autogenerate 与测试 create_all 使用。

新增模块的 models.py 必须在这里 import,否则迁移看不见。
"""

from app.core import audit, outbox, platform_config, policies, ratelimit
from app.core.db import Base
from app.modules.account import models as account_models
from app.modules.adminapi import models as adminapi_models
from app.modules.billing import models as billing_models
from app.modules.catalog import models as catalog_models
from app.modules.legal import models as legal_models
from app.modules.metering import models as metering_models
from app.modules.nodes import models as nodes_models
from app.modules.notify import models as notify_models
from app.modules.orchestrator import models as orchestrator_models
from app.modules.tickets import models as tickets_models

__all__ = [
    "Base",
    "account_models",
    "adminapi_models",
    "audit",
    "billing_models",
    "catalog_models",
    "legal_models",
    "metering_models",
    "nodes_models",
    "notify_models",
    "orchestrator_models",
    "outbox",
    "platform_config",
    "policies",
    "ratelimit",
    "tickets_models",
]


# ---------- 跨模块索引声明(元数据登记点) ----------
# alembic check 要求 DB 里的每个索引都在 metadata 有声明;下列索引落在其他模块拥有的表上,
# 统一登记在这里,与各表迁移保持同一事实源。
from sqlalchemy import CheckConstraint, Index, text

Index(
    "ix_outbox_tasks_status_next_retry_at_id",  # 领取查询 ORDER BY next_retry_at, id
    outbox.OutboxTask.__table__.c.status,
    outbox.OutboxTask.__table__.c.next_retry_at,
    outbox.OutboxTask.__table__.c.id,
)
Index(  # 冻结/停用小众状态的反查(冻结租户巡检等);active 是大头不入索引
    "ix_users_status_not_active",
    account_models.User.__table__.c.status,
    postgresql_where=text("status <> 'active'"),
)
Index(  # 审计按操作人下钻(actor_id, 按 id 倒序翻页)
    "ix_audit_log_actor_id_id",
    audit.AuditLog.__table__.c.actor_id,
    audit.AuditLog.__table__.c.id,
)
Index(  # 用户账单页按 (user_id, hour_start) 翻页
    "ix_bills_hourly_user_hour",
    billing_models.BillHourly.__table__.c.user_id,
    billing_models.BillHourly.__table__.c.hour_start,
)
Index(  # 用户盘账单页按 (user_id, day) 翻页
    "ix_bills_daily_disk_user_day",
    billing_models.BillDailyDisk.__table__.c.user_id,
    billing_models.BillDailyDisk.__table__.c.day,
)

# CHECK 兜底:同一迁移 NOT VALID + VALIDATE 落库,谓词文本与迁移保持一致;
# alembic 按名字比对,naming convention 自动补 ck_<表>_ 前缀,故声明短名。
# instances.status 的同款声明在 orchestrator/models.py。
_EXTRA_CHECKS: list[tuple[CheckConstraint, str]] = [
    (
        CheckConstraint(
            "ref_type IS NULL OR ref_type IN"
            " ('bill_hourly', 'bill_daily_disk', 'order', 'adjustment', 'refund_request')",
            name="ref_type",
        ),
        "balance_ledger",
    ),
    (CheckConstraint("amount <> 0", name="amount_nonzero"), "admin_adjustments"),
    (
        CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="status"),
        "admin_adjustments",
    ),
    (
        CheckConstraint(
            "reviewed_by IS NULL OR reviewed_by <> created_by",
            name="reviewer_not_creator",
        ),
        "admin_adjustments",
    ),
    (
        CheckConstraint("status IN ('pending', 'paid', 'failed', 'closed')", name="status"),
        "orders",
    ),
    (
        CheckConstraint(
            "status IN ('pending', 'running', 'done', 'dead', 'discarded')",
            name="status",
        ),
        "outbox_tasks",
    ),
    (CheckConstraint("id = 1", name="singleton"), "cluster_status"),
]
for _ck, _table in _EXTRA_CHECKS:
    Base.metadata.tables[_table].append_constraint(_ck)
