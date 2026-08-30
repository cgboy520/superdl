from typing import Annotated

from fastapi import APIRouter, Header, Request, Response

from app.core.audit import set_audit_target
from app.core.db import DbSession
from app.core.http import mark_idempotent_replay
from app.core.pagination import Page
from app.core.params import Cursor, Limit
from app.modules.account.deps import CurrentUser
from app.modules.tickets import service
from app.modules.tickets.schemas import (
    TicketCreate,
    TicketDetailOut,
    TicketMessageCreate,
    TicketMessageOut,
    TicketOut,
)

router = APIRouter(tags=["tickets"])


@router.post("/tickets", status_code=201)
async def create_ticket(
    body: TicketCreate,
    user: CurrentUser,
    session: DbSession,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TicketOut:
    """创建工单(首条消息同单提交)。Idempotency-Key 重放返回既有单(200 +
    X-Idempotent-Replay);进行中 ≤10,限流 5/h。"""
    ticket, created = await service.create_ticket(
        session,
        user.id,
        category=body.category,
        subject=body.subject,
        body=body.body,
        instance_uuid=body.instance_uuid,
        idempotency_key=idempotency_key,
    )
    if not created:
        mark_idempotent_replay(response)
    set_audit_target(
        request,
        f"user:{user.id}",
        detail={"ticket_no": ticket.ticket_no, "category": ticket.category},
    )
    return TicketOut.model_validate(ticket)


@router.get("/tickets")
async def list_my_tickets(
    user: CurrentUser,
    session: DbSession,
    cursor: str | None = Cursor,
    limit: int | None = Limit,
) -> Page[TicketOut]:
    """本人工单(游标分页)。"""
    return await service.list_my_tickets(session, user.id, cursor=cursor, limit=limit)


@router.get("/tickets/{ticket_id}")
async def get_my_ticket(ticket_id: int, user: CurrentUser, session: DbSession) -> TicketDetailOut:
    """工单详情 + 消息流。owner 校验在 SQL WHERE(他人工单与不存在同回 404)。"""
    return await service.get_my_ticket(session, user.id, ticket_id)


@router.post("/tickets/{ticket_id}/messages", status_code=201)
async def append_message(
    ticket_id: int, body: TicketMessageCreate, user: CurrentUser, session: DbSession
) -> TicketMessageOut:
    """追加回复(→ pending_staff);resolved/closed 不可再回复。"""
    msg = await service.append_message(session, user.id, ticket_id, body=body.body)
    return TicketMessageOut.model_validate(msg)


@router.post("/tickets/{ticket_id}/close")
async def close_ticket(ticket_id: int, user: CurrentUser, session: DbSession) -> TicketOut:
    """关闭工单(仅 resolved;closed_at 仅此路径落)。"""
    ticket = await service.close_ticket(session, user.id, ticket_id)
    return TicketOut.model_validate(ticket)
