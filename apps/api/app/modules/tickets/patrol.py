"""Stale ticket patrol (every 30 minutes): pending_staff for more than 24 h → admin_alerts warning;
dedup_key = ticket-stale:{ticket_id}, reported once per ticket.
"""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.locks import LockKey, advisory_lock
from app.core.logging import get_logger
from app.core.servercopy import copy as server_copy
from app.core.timeutil import now_utc
from app.modules.notify import service as notify_service
from app.modules.tickets import service as tickets_service

logger = get_logger(__name__)

STALE_AFTER = timedelta(hours=24)


async def stale_ticket_patrol(sm: async_sessionmaker[AsyncSession]) -> int:
    """Scan stale tickets and write warning alerts. Returns the number of new alerts this round."""
    alerted = 0
    async with advisory_lock(sm, LockKey.TICKET_STALE_PATROL) as got:
        if not got:
            return 0
        async with sm() as session:
            stale = await tickets_service.list_stale_pending_staff(
                session, older_than=now_utc() - STALE_AFTER
            )
            for ticket in stale:
                ok = await notify_service.notify(
                    session,
                    None,
                    type_="admin_alert",
                    title=server_copy("tickets.stale.title"),
                    content=server_copy(
                        "tickets.stale.content",
                        ticket_no=ticket.ticket_no,
                        category=ticket.category,
                        subject=ticket.subject,
                    ),
                    severity="warning",
                    dedup_key=f"ticket-stale:{ticket.id}",
                    target_id=str(ticket.id),
                    target_kind="ticket",
                )
                if ok:
                    alerted += 1
            await session.commit()
    if alerted:
        logger.warning("ticket_stale_patrol_alerts", alerted=alerted)
    return alerted
