from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

TicketCategory = Literal["instance", "billing", "data", "account", "other"]


class TicketCreate(BaseModel):
    """创建工单(首条消息同单提交)。instance_uuid 可选关联实例。"""

    category: TicketCategory
    subject: str = Field(min_length=2, max_length=128)
    body: str = Field(min_length=2, max_length=4000)
    instance_uuid: str | None = Field(default=None, max_length=32)

    @field_validator("subject", "body", "instance_uuid", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


class TicketMessageCreate(BaseModel):
    body: str = Field(min_length=2, max_length=4000)

    @field_validator("body", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


class TicketMessageOut(BaseModel):
    id: int
    sender_kind: str
    body: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TicketOut(BaseModel):
    """用户端工单视图(列表项)。不透出 sender_id 等内部字段。"""

    id: int
    ticket_no: str
    category: str
    subject: str
    status: str
    instance_uuid: str | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None

    model_config = {"from_attributes": True}


class TicketDetailOut(TicketOut):
    """工单详情 + 消息流(时间升序)。"""

    messages: list[TicketMessageOut] = []


class AdminTicketOut(TicketOut):
    """管理端工单视图:比用户端多租户 id。"""

    user_id: int


class AdminTicketDetailOut(AdminTicketOut):
    messages: list[TicketMessageOut] = []


class AdminTicketReply(BaseModel):
    body: str = Field(min_length=2, max_length=4000)

    @field_validator("body", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


class AdminTicketStatusUpdate(BaseModel):
    """管理端状态操作:resolve=标记解决;close=关闭(仅 resolved 后可)。"""

    action: Literal["resolve", "close"]
