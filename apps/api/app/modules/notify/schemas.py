"""notify 对外 schema。"""

from datetime import datetime

from pydantic import BaseModel


class NotificationOut(BaseModel):
    id: int
    type: str
    title: str
    content: str
    severity: str
    # 结构化跳转目标(instance 类 = 实例 uuid,ticket 类 = 工单 id;空 = 前端按类型落列表页)
    target_id: str | None = None
    read_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class UnreadCountOut(BaseModel):
    """未读角标轻端点(顶栏 30s 轮询)。"""

    unread_count: int
