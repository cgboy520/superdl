"""notify 对外 schema(模块边界:其他模块只许 import 本文件与 service)。"""

from datetime import datetime

from pydantic import BaseModel


class NotificationOut(BaseModel):
    id: int
    type: str
    title: str
    content: str
    severity: str
    read_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
