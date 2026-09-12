"""工单滞留巡检(30 分钟):pending_staff 超 24h → admin_alerts warning;
dedup_key = ticket-stale:{ticket_id},同一工单只报一次。
"""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.locks import LockKey, advisory_lock
from app.core.logging import get_logger
from app.core.timeutil import now_utc
from app.modules.notify import service as notify_service
from app.modules.tickets import service as tickets_service

logger = get_logger(__name__)

STALE_AFTER = timedelta(hours=24)


async def stale_ticket_patrol(sm: async_sessionmaker[AsyncSession]) -> int:
    """扫描滞留工单并落 warning 告警。返回本轮新增告警数。"""
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
                    title="工单滞留超 24h",
                    content=(
                        f"{ticket.ticket_no} [{ticket.category}] {ticket.subject} "
                        "等待客服回复已超过 24 小时,请尽快处理。"
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
