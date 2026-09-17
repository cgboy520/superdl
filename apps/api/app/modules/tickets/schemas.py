from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

TicketCategory = Literal["instance", "billing", "data", "account", "other"]


class TicketCreate(BaseModel):
    """Create a ticket (the first message is submitted with it). instance_uuid optionally links an
    instance."""

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
    """User ticket view (list item)."""

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
    """Ticket detail + message stream (ascending by time)."""

    messages: list[TicketMessageOut] = []


class AdminTicketOut(TicketOut):
    """Admin ticket view: the user view plus the tenant id."""

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
    """Admin status action: resolve = mark resolved; close = close (only after resolved)."""

    action: Literal["resolve", "close"]


class AdminTicketCountOut(BaseModel):
    """Lightweight ticket count endpoint (pending badge polling)."""

    count: int
