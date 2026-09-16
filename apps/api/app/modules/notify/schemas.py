"""Public schemas of notify."""

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
    """Lightweight unread badge endpoint (top bar polls every 30 s)."""

    unread_count: int
