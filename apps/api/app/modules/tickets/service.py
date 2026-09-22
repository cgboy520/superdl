"""Ticket creation, replies and closing; user actions query by ownership, status changes hold the
row lock.

resolved/closed accept no replies, closing requires resolved first.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode, conflict, not_found
from app.core.idempotency import find_replay, insert_idempotent
from app.core.logging import get_logger
from app.core.pagination import Page, paginate_by_id
from app.core.ratelimit import check_rate_limit
from app.core.servercopy import copy as server_copy
from app.core.sqlutil import next_daily_seq
from app.core.timeutil import now_utc
from app.modules.notify import service as notify_service
from app.modules.tickets.models import Ticket, TicketMessage
from app.modules.tickets.schemas import (
    AdminTicketDetailOut,
    AdminTicketOut,
    TicketDetailOut,
    TicketMessageOut,
    TicketOut,
)

logger = get_logger(__name__)

ACTIVE_STATUSES = ("open", "pending_staff", "pending_user")
TERMINAL_STATUSES = ("resolved", "closed")

MAX_OPEN_TICKETS = 10
CREATE_RATE_LIMIT = 5
CREATE_RATE_WINDOW = 3600.0


async def list_stale_pending_staff(session: AsyncSession, *, older_than: datetime) -> list[Ticket]:
    """pending_staff tickets whose updated_at is older than the threshold, by id ascending."""
    return list(
        (
            await session.execute(
                select(Ticket)
                .where(Ticket.status == "pending_staff", Ticket.updated_at < older_than)
                .order_by(Ticket.id)
            )
        ).scalars()
    )


async def _admin_alert(
    session: AsyncSession, *, title: str, content: str, dedup_key: str, ticket_id: int
) -> None:
    """Write one info row into the admin alert feed (notify table type='admin_alert',
    user_id=NULL), deep-linked to the ticket."""
    await notify_service.notify(
        session,
        None,
        type_="admin_alert",
        title=title,
        content=content,
        severity="info",
        dedup_key=dedup_key,
        target_id=str(ticket_id),
        target_kind="ticket",
    )


async def create_ticket(
    session: AsyncSession,
    user_id: int,
    *,
    category: str,
    subject: str,
    body: str,
    instance_uuid: str | None,
    idempotency_key: str | None,
) -> tuple[Ticket, bool]:
    """Commit the ticket, first message and admin alert together. Returns (ticket, created);
    created=False means idempotent replay."""
    if idempotency_key:
        existing = await find_replay(
            session, Ticket, owner_col=Ticket.user_id, owner_id=user_id, key=idempotency_key
        )
        if existing is not None:
            return existing, False

    await check_rate_limit(
        f"ticket-create:{user_id}",
        max_attempts=CREATE_RATE_LIMIT,
        window_seconds=CREATE_RATE_WINDOW,
    )
    open_count = (
        await session.execute(
            select(func.count()).where(
                Ticket.user_id == user_id, Ticket.status.in_(ACTIVE_STATUSES)
            )
        )
    ).scalar_one()
    if open_count >= MAX_OPEN_TICKETS:
        raise conflict(key="tickets.openLimitReached", params={"max": MAX_OPEN_TICKETS})

    prefix = f"T{now_utc():%Y%m%d}"
    for _ in range(8):
        ticket = Ticket(
            ticket_no=f"{prefix}-{await next_daily_seq(session, Ticket.ticket_no, prefix):02d}",
            user_id=user_id,
            category=category,
            subject=subject,
            instance_uuid=instance_uuid,
            idempotency_key=idempotency_key,
        )
        try:
            result = await insert_idempotent(
                session,
                ticket,
                model=Ticket,
                owner_col=Ticket.user_id,
                owner_id=user_id,
                key=idempotency_key,
            )
        except IntegrityError:
            continue
        if result is not ticket:
            return result, False
        session.add(
            TicketMessage(ticket_id=ticket.id, sender_kind="user", sender_id=user_id, body=body)
        )
        break
    else:
        raise AppError(ErrorCode.INTERNAL, key="common.internal", http_status=500)

    await _admin_alert(
        session,
        title=server_copy("tickets.created.title"),
        content=f"{ticket.ticket_no} [{category}] {subject}",
        dedup_key=f"ticket:created:{ticket.id}",
        ticket_id=ticket.id,
    )
    await session.commit()
    logger.info("ticket_created", ticket_no=ticket.ticket_no, user_id=user_id, category=category)
    return ticket, True


async def list_my_tickets(
    session: AsyncSession, user_id: int, *, cursor: str | None = None, limit: int | None = None
) -> Page[TicketOut]:
    """The caller's tickets (cursor pagination)."""
    stmt = select(Ticket).where(Ticket.user_id == user_id).order_by(Ticket.id.desc())
    page_items, next_cursor = await paginate_by_id(
        session, stmt, id_col=Ticket.id, cursor=cursor, limit=limit
    )
    return Page[TicketOut](
        items=[TicketOut.model_validate(r) for r in page_items], next_cursor=next_cursor
    )


MAX_MESSAGES_PER_TICKET = 200


async def _messages_of(session: AsyncSession, ticket_id: int) -> list[TicketMessageOut]:
    rows = (
        await session.execute(
            select(TicketMessage)
            .where(TicketMessage.ticket_id == ticket_id)
            .order_by(TicketMessage.id.asc())
            .limit(MAX_MESSAGES_PER_TICKET + 1)
        )
    ).scalars()
    return [TicketMessageOut.model_validate(r) for r in rows]


async def get_my_ticket(session: AsyncSession, user_id: int, ticket_id: int) -> TicketDetailOut:
    """Ticket detail (own); someone else's ticket and a missing one both return 404."""
    ticket = (
        await session.execute(
            select(Ticket).where(Ticket.id == ticket_id, Ticket.user_id == user_id)
        )
    ).scalar_one_or_none()
    if ticket is None:
        raise not_found(key="tickets.notFound")
    return TicketDetailOut(
        **TicketOut.model_validate(ticket).model_dump(),
        messages=await _messages_of(session, ticket.id),
    )


async def _get_my_for_update(session: AsyncSession, user_id: int, ticket_id: int) -> Ticket:
    ticket = (
        await session.execute(
            select(Ticket)
            .where(Ticket.id == ticket_id, Ticket.user_id == user_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if ticket is None:
        raise not_found(key="tickets.notFound")
    return ticket


def _ensure_repliable(ticket: Ticket) -> None:
    if ticket.status in TERMINAL_STATUSES:
        raise conflict(key="tickets.stateNotRepliable", params={"status": ticket.status})


async def append_message(
    session: AsyncSession, user_id: int, ticket_id: int, *, body: str
) -> TicketMessage:
    """User reply (status transition under the row lock → pending_staff); an admin_alerts info row
    tells the on-call."""
    await check_rate_limit(f"ticket-reply:{user_id}", max_attempts=30, window_seconds=600.0)
    ticket = await _get_my_for_update(session, user_id, ticket_id)
    _ensure_repliable(ticket)
    count = (
        await session.execute(select(func.count()).where(TicketMessage.ticket_id == ticket.id))
    ).scalar_one()
    if count >= MAX_MESSAGES_PER_TICKET:
        raise conflict(key="tickets.messageLimitReached", params={"max": MAX_MESSAGES_PER_TICKET})
    msg = TicketMessage(ticket_id=ticket.id, sender_kind="user", sender_id=user_id, body=body)
    session.add(msg)
    ticket.status = "pending_staff"
    await session.flush()
    await _admin_alert(
        session,
        title=server_copy("tickets.reply.title"),
        content=f"{ticket.ticket_no} {ticket.subject}",
        dedup_key=f"ticket:user-reply:{msg.id}",
        ticket_id=ticket.id,
    )
    await session.commit()
    logger.info("ticket_user_reply", ticket_no=ticket.ticket_no, message_id=msg.id)
    return msg


async def close_ticket(session: AsyncSession, user_id: int, ticket_id: int) -> Ticket:
    """User close (resolved only; closed_at is set only on closed)."""
    ticket = await _get_my_for_update(session, user_id, ticket_id)
    if ticket.status != "resolved":
        raise conflict(key="tickets.stateNotClosable", params={"status": ticket.status})
    ticket.status = "closed"
    ticket.closed_at = now_utc()
    await session.commit()
    await session.refresh(ticket)
    logger.info("ticket_closed", ticket_no=ticket.ticket_no, by="user")
    return ticket


async def admin_list_tickets(
    session: AsyncSession,
    status: str | None = None,
    category: str | None = None,
    *,
    user_id: int | None = None,
    ticket_no: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> Page[AdminTicketOut]:
    """Ticket list (cursor pagination, descending): status/category exact filters, user_id/ticket_no
    search."""
    stmt = select(Ticket).order_by(Ticket.id.desc())
    if status:
        stmt = stmt.where(Ticket.status == status)
    if category:
        stmt = stmt.where(Ticket.category == category)
    if user_id is not None:
        stmt = stmt.where(Ticket.user_id == user_id)
    if ticket_no:
        stmt = stmt.where(Ticket.ticket_no == ticket_no.strip())
    page_items, next_cursor = await paginate_by_id(
        session, stmt, id_col=Ticket.id, cursor=cursor, limit=limit
    )
    return Page[AdminTicketOut](
        items=[AdminTicketOut.model_validate(r) for r in page_items], next_cursor=next_cursor
    )


async def admin_count_tickets(
    session: AsyncSession, status: str | None = None, category: str | None = None
) -> int:
    """Count tickets filtered by optional exact status and category."""
    stmt = select(func.count()).select_from(Ticket)
    if status:
        stmt = stmt.where(Ticket.status == status)
    if category:
        stmt = stmt.where(Ticket.category == category)
    return int((await session.execute(stmt)).scalar_one())


async def _get_for_update(session: AsyncSession, ticket_id: int) -> Ticket:
    ticket = await session.get(Ticket, ticket_id, with_for_update=True)
    if ticket is None:
        raise not_found(key="tickets.notFound")
    return ticket


async def admin_get_ticket(session: AsyncSession, ticket_id: int) -> AdminTicketDetailOut:
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        raise not_found(key="tickets.notFound")
    return AdminTicketDetailOut(
        **AdminTicketOut.model_validate(ticket).model_dump(),
        messages=await _messages_of(session, ticket.id),
    )


async def admin_reply(
    session: AsyncSession, ticket_id: int, *, body: str, operator_id: int
) -> TicketMessage:
    """Staff reply (status transition under the row lock → pending_user); in-app notification to the
    user (dedup_key against duplicates)."""
    ticket = await _get_for_update(session, ticket_id)
    _ensure_repliable(ticket)
    msg = TicketMessage(ticket_id=ticket.id, sender_kind="staff", sender_id=operator_id, body=body)
    session.add(msg)
    ticket.status = "pending_user"
    await session.flush()
    await notify_service.notify(
        session,
        ticket.user_id,
        type_="ticket",
        title=server_copy("tickets.reply.title"),
        content=server_copy(
            "tickets.staff_reply.content", ticket_no=ticket.ticket_no, subject=ticket.subject
        ),
        dedup_key=f"ticket:staff-reply:{msg.id}",
        target_id=str(ticket.id),
    )
    await session.commit()
    logger.info("ticket_staff_reply", ticket_no=ticket.ticket_no, operator_id=operator_id)
    return msg


async def admin_update_status(session: AsyncSession, ticket_id: int, *, action: str) -> Ticket:
    """Mark resolved / close (status transition under the row lock). close only after resolved
    (closed_at is set only on closed)."""
    ticket = await _get_for_update(session, ticket_id)
    if action == "resolve":
        if ticket.status in TERMINAL_STATUSES:
            raise conflict(key="tickets.stateNotResolvable", params={"status": ticket.status})
        ticket.status = "resolved"
    else:
        if ticket.status != "resolved":
            raise conflict(key="tickets.stateNotClosable", params={"status": ticket.status})
        ticket.status = "closed"
        ticket.closed_at = now_utc()
    await session.commit()
    await session.refresh(ticket)
    logger.info("ticket_status", ticket_no=ticket.ticket_no, action=action)
    return ticket
