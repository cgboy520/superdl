"""notify 对外 schema。"""

from datetime import datetime

from pydantic import BaseModel


class NotificationOut(BaseModel):
    id: int
    type: str
    title: str
    content: str
    severity: str
    target_id: str | None = None
    read_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class UnreadCountOut(BaseModel):
    """未读角标轻端点(顶栏 30s 轮询)。"""

    unread_count: int
