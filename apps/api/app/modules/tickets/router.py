from fastapi import APIRouter, Request, Response

from app.core.audit import set_audit_target
from app.core.db import DbSession
from app.core.http import mark_idempotent_replay
from app.core.pagination import Page
from app.core.params import Cursor, IdempotencyKey, Limit
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
    idempotency_key: IdempotencyKey = None,
) -> TicketOut:
    """Create a ticket (the first message is submitted with it). An Idempotency-Key replay returns
    the existing ticket (200 +
    X-Idempotent-Replay); at most 10 open, rate limit 5/h."""
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
    """The caller's tickets (cursor pagination)."""
    return await service.list_my_tickets(session, user.id, cursor=cursor, limit=limit)


@router.get("/tickets/{ticket_id}")
async def get_my_ticket(ticket_id: int, user: CurrentUser, session: DbSession) -> TicketDetailOut:
    """Ticket detail + message stream; someone else's ticket and a missing one both return 404."""
    return await service.get_my_ticket(session, user.id, ticket_id)


@router.post("/tickets/{ticket_id}/messages", status_code=201)
async def append_message(
    ticket_id: int, body: TicketMessageCreate, user: CurrentUser, session: DbSession
) -> TicketMessageOut:
    """Append a reply (→ pending_staff); resolved/closed accept no more replies."""
    msg = await service.append_message(session, user.id, ticket_id, body=body.body)
    return TicketMessageOut.model_validate(msg)


@router.post("/tickets/{ticket_id}/close")
async def close_ticket(ticket_id: int, user: CurrentUser, session: DbSession) -> TicketOut:
    """Close the ticket (resolved only; closed_at is set only on closed)."""
    ticket = await service.close_ticket(session, user.id, ticket_id)
    return TicketOut.model_validate(ticket)
