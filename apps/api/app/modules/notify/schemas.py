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


class UnreadCountOut(BaseModel):
    """未读角标轻端点:顶栏 30s 轮询用,不拉通知列表全页。"""

    unread_count: int
