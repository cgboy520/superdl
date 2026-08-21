"""聚合全部 SQLAlchemy 模型,供 alembic autogenerate 与测试 create_all 使用。

新增模块的 models.py 必须在这里 import,否则迁移看不见。
"""

from app.core import audit, outbox, platform_config, policies, ratelimit
from app.core.db import Base
from app.modules.account import models as account_models
from app.modules.adminapi import models as adminapi_models
from app.modules.billing import models as billing_models
from app.modules.catalog import models as catalog_models
from app.modules.metering import models as metering_models
from app.modules.nodes import models as nodes_models
from app.modules.notify import models as notify_models
from app.modules.orchestrator import models as orchestrator_models

__all__ = [
    "Base",
    "account_models",
    "adminapi_models",
    "audit",
    "billing_models",
    "catalog_models",
    "metering_models",
    "nodes_models",
    "notify_models",
    "orchestrator_models",
    "outbox",
    "platform_config",
    "policies",
    "ratelimit",
]
