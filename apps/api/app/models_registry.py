"""聚合全部 SQLAlchemy 模型,供 alembic autogenerate 与测试 create_all 使用。

新增模块的 models.py 必须在这里 import,否则迁移看不见。
"""

from app.core import audit, outbox
from app.core.db import Base

__all__ = ["Base", "audit", "outbox"]
