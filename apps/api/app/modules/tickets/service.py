"""工单闭环:用户创建 → 客服/用户交替回复 → 标记解决 → 关闭。
- 用户侧 owner 校验在 SQL WHERE(id + user_id),查不到即 404。
- 状态迁移只在行锁内:open → pending_staff → pending_user → resolved → closed(仅 resolved 后);
  resolved/closed 不可再回复。
- 创建幂等((user_id, idempotency_key) 唯一);每用户进行中 ≤ 10;创建限流 5/h。
- 联动:客服回复 → 用户站内信;新工单/用户回复 → admin_alerts info。
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

# 进行中状态(计入每用户 ≤10 上限,也是可回复状态)
ACTIVE_STATUSES = ("open", "pending_staff", "pending_user")
TERMINAL_STATUSES = ("resolved", "closed")

MAX_OPEN_TICKETS = 10
CREATE_RATE_LIMIT = 5  # 次/小时
CREATE_RATE_WINDOW = 3600.0


async def list_stale_pending_staff(session: AsyncSession, *, older_than: datetime) -> list[Ticket]:
    """滞留工单:pending_staff(等客服回复)且最后更新时间早于阈值。供滞留巡检告警。"""
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
    """管理端告警流落一条 info(notify 表 type='admin_alert',user_id=NULL),深链到该工单。"""
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
    """创建工单(首条消息同单落)。返回 (工单, created);created=False = 幂等重放。"""
    if idempotency_key:
        existing = await find_replay(
            session, Ticket, owner_col=Ticket.user_id, owner_id=user_id, key=idempotency_key
        )
        if existing is not None:
            return existing, False  # 幂等重放

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

    # ticket_no = T+yyyymmdd+两位日内序列;撞唯一索引换下一个序列重试
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
            continue  # 按 ticket_no 序列撞车处理:重试下一序列
        if result is not ticket:
            return result, False  # 同键并发:返回胜出方的单
        # 首条消息同单 commit
        session.add(
            TicketMessage(ticket_id=ticket.id, sender_kind="user", sender_id=user_id, body=body)
        )
        await session.commit()
        break
    else:
        raise AppError(ErrorCode.INTERNAL, key="common.internal", http_status=500)

    logger.info("ticket_created", ticket_no=ticket.ticket_no, user_id=user_id, category=category)
    await _admin_alert(
        session,
        title="新工单",
        content=f"{ticket.ticket_no} [{category}] {subject}",
        dedup_key=f"ticket:created:{ticket.id}",
        ticket_id=ticket.id,
    )
    await session.commit()
    return ticket, True


async def list_my_tickets(
    session: AsyncSession, user_id: int, *, cursor: str | None = None, limit: int | None = None
) -> Page[TicketOut]:
    """本人工单(游标分页)。"""
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
    """工单详情(本人);他人工单与不存在同回 404。"""
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
    """用户追加回复(行锁内状态迁移 → pending_staff);admin_alerts info 告知值班。"""
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
    await session.commit()
    logger.info("ticket_user_reply", ticket_no=ticket.ticket_no, message_id=msg.id)
    await _admin_alert(
        session,
        title="工单有新回复",
        content=f"{ticket.ticket_no} {ticket.subject}",
        dedup_key=f"ticket:user-reply:{msg.id}",
        ticket_id=ticket.id,
    )
    await session.commit()
    return msg


async def close_ticket(session: AsyncSession, user_id: int, ticket_id: int) -> Ticket:
    """用户关闭(仅 resolved;closed_at 仅 closed 落)。"""
    ticket = await _get_my_for_update(session, user_id, ticket_id)
    if ticket.status != "resolved":
        raise conflict(key="tickets.stateNotClosable", params={"status": ticket.status})
    ticket.status = "closed"
    ticket.closed_at = now_utc()
    await session.commit()
    await session.refresh(ticket)  # updated_at 是 onupdate SQL 表达式,UPDATE 后已被 expire
    logger.info("ticket_closed", ticket_no=ticket.ticket_no, by="user")
    return ticket


# ---------- 管理端(读 ops/finance/readonly,写 ops/admin) ----------


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
    """工单列表(游标分页,降序):status/category 精确过滤,user_id/ticket_no 检索。"""
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
    """工单计数(待办角标轻端点),与列表同一过滤口径。"""
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
    """客服回复(行锁内状态迁移 → pending_user);站内信告知用户(dedup_key 防重)。"""
    ticket = await _get_for_update(session, ticket_id)
    _ensure_repliable(ticket)
    msg = TicketMessage(ticket_id=ticket.id, sender_kind="staff", sender_id=operator_id, body=body)
    session.add(msg)
    ticket.status = "pending_user"
    await session.flush()  # 先取 msg.id:站内信 dedup_key 以消息粒度防重
    await notify_service.notify(
        session,
        ticket.user_id,
        type_="ticket",
        title="工单有新回复",
        content=f"您的工单 {ticket.ticket_no}({ticket.subject})客服已回复,请前往「支持」查看。",
        dedup_key=f"ticket:staff-reply:{msg.id}",
        target_id=str(ticket.id),
    )
    await session.commit()
    logger.info("ticket_staff_reply", ticket_no=ticket.ticket_no, operator_id=operator_id)
    return msg


async def admin_update_status(session: AsyncSession, ticket_id: int, *, action: str) -> Ticket:
    """标记解决/关闭(行锁内状态迁移)。close 仅 resolved 后可(closed_at 仅 closed 落)。"""
    ticket = await _get_for_update(session, ticket_id)
    if action == "resolve":
        if ticket.status in TERMINAL_STATUSES:
            raise conflict(key="tickets.stateNotResolvable", params={"status": ticket.status})
        ticket.status = "resolved"
    else:  # close
        if ticket.status != "resolved":
            raise conflict(key="tickets.stateNotClosable", params={"status": ticket.status})
        ticket.status = "closed"
        ticket.closed_at = now_utc()
    await session.commit()
    await session.refresh(ticket)  # updated_at 是 onupdate SQL 表达式,UPDATE 后已被 expire
    logger.info("ticket_status", ticket_no=ticket.ticket_no, action=action)
    return ticket
