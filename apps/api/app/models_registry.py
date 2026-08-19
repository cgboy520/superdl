"""聚合全部 SQLAlchemy 模型,供 alembic autogenerate 与测试 create_all 使用。

新增模块的 models.py 必须在这里 import,否则迁移看不见。
"""

from app.core import audit, outbox
from app.core.db import Base
from app.modules.account import models as account_models
from app.modules.adminapi import models as adminapi_models
from app.modules.catalog import models as catalog_models

__all__ = ["Base", "account_models", "adminapi_models", "audit", "catalog_models", "outbox"]
