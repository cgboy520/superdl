"""In-app notifications, asynchronous outbox SMS and alert ingestion; dedup_key deduplicates."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.constants import ADMIN_LIST_CAP
from app.core.errors import AppError, conflict, not_found
from app.core.idempotency import find_replay, insert_idempotent
from app.core.logging import get_logger
from app.core.outbox import OutboxTask, RetryPolicy, enqueue, outbox_handler
from app.core.pagination import Page, RawPage, paginate_by_id
from app.core.platform_config import get_runtime_config
from app.core.ratelimit import check_rate_limit
from app.core.servercopy import copy as server_copy
from app.core.sms import ensure_sms_platform_quota, get_sms_channel
from app.core.timeutil import now_utc
from app.modules.account import service as account_service
from app.modules.account.service import get_user, list_active_user_ids
from app.modules.notify.models import Announcement, Notification
from app.modules.notify.schemas import NotificationOut

if TYPE_CHECKING:
    from app.core.pagination import Page
    from app.modules.notify.schemas import NotificationOut

logger = get_logger(__name__)


def _day_bucket(now: datetime) -> str:
    return f"{now:%Y%m%d}"


async def notify(
    session: AsyncSession,
    user_id: int | None,
    *,
    type_: str,
    title: str,
    content: str,
    severity: str = "info",
    dedup_key: str | None = None,
    target_id: str | None = None,
    target_kind: str | None = None,
    sms: bool = False,
) -> bool:
    """Write an in-app notification (optionally SMS, enqueuing notify.sms in the same transaction).
    Returns False on a dedup_key conflict. No commit.
    target_id: navigation target (instance types = instance uuid, ticket types = ticket id), empty
    without a target;
    target_kind only for the admin alert feed (tenant / node / ticket), decides the deep link.
    """
    result = (
        await session.execute(
            pg_insert(Notification)
            .values(
                user_id=user_id,
                type=type_,
                title=title,
                content=content,
                severity=severity,
                dedup_key=dedup_key,
                target_id=target_id,
                target_kind=target_kind,
            )
            .on_conflict_do_nothing(index_elements=["dedup_key"])
            .returning(Notification.id)
        )
    ).scalar_one_or_none()
    if result is None:
        return False
    if sms and user_id is not None:
        enqueue(session, SMS_TASK_TYPE, {"user_id": user_id, "title": title})
    return True


SMS_TASK_TYPE = "notify.sms"


@outbox_handler(SMS_TASK_TYPE, retry=RetryPolicy(timeout_seconds=60))
async def handle_notify_sms(session: AsyncSession, task: OutboxTask) -> None:
    """Send the notification SMS to payload.phone or the user; a missing recipient or a failed
    budget
    check returns without retry."""
    phone: str | None = task.payload.get("phone")
    user_id = task.payload.get("user_id")
    if phone is None and user_id is not None:
        try:
            user = await get_user(session, user_id)
        except AppError:
            logger.warning("sms_user_missing", user_id=user_id, task_id=task.id)
            return
        phone = user.phone
    if not phone:
        logger.warning("sms_no_recipient", task_id=task.id)
        return
    channel = await get_sms_channel(session)
    try:
        await ensure_sms_platform_quota("notify")
    except AppError:
        logger.warning("sms_platform_quota_exhausted", task_id=task.id)
        return
    await channel.send(phone, "notice", {"title": task.payload["title"]})


async def send_low_balance_warning(
    session: AsyncSession, user_id: int, *, est_hours: float, balance: str
) -> None:
    """Write the balance notification and SMS task deduplicated per user and UTC calendar day, then
    commit."""
    await notify(
        session,
        user_id,
        type_="balance_warn",
        title=server_copy("notify.low_balance.title"),
        content=server_copy(
            "notify.low_balance.content", balance=balance, hours=f"{est_hours:.1f}"
        ),
        severity="warning",
        dedup_key=f"balance_warn:{user_id}:{_day_bucket(now_utc())}",
        sms=True,
    )
    await session.commit()


async def send_arrears_notice(
    session: AsyncSession, user_id: int, *, action: str, detail: str
) -> None:
    """Write the arrears notification and SMS task per action, user and UTC day bucket; the caller
    commits."""
    key = action if action in ("auto_stop", "freeze", "reclaim") else "default"
    await notify(
        session,
        user_id,
        type_="arrears",
        title=server_copy(f"notify.arrears.{key}"),
        content=detail,
        severity="warning",
        dedup_key=f"arrears:{action}:{user_id}:{_day_bucket(now_utc())}",
        sms=True,
    )


async def send_subscription_notice(
    session: AsyncSession,
    user_id: int,
    *,
    action: str,
    detail: str,
    dedup_suffix: str,
    target_id: str | None = None,
) -> None:
    """Write the subscription notification and SMS task per action, dedup_suffix and UTC day
    bucket; the caller commits."""
    key = action if action in ("expiring", "expired", "renewed", "renew_failed") else "default"
    await notify(
        session,
        user_id,
        type_="subscription",
        title=server_copy(f"notify.subscription.{key}"),
        content=detail,
        severity="info" if action == "renewed" else "warning",
        dedup_key=f"subscription:{action}:{dedup_suffix}:{_day_bucket(now_utc())}",
        target_id=target_id,
        sms=True,
    )


async def send_preemption_notice(
    session: AsyncSession,
    user_id: int,
    *,
    instance_name: str,
    grace_seconds: int,
    instance_id: int,
    instance_uuid: str,
) -> None:
    """Notification of a preempted spot instance (in-app + SMS); dedup_key = instance id + minute,
    not bucketed by day."""
    await notify(
        session,
        user_id,
        type_="preempted",
        title=server_copy("notify.preempted.title"),
        content=server_copy("notify.preempted.content", name=instance_name, seconds=grace_seconds),
        severity="warning",
        dedup_key=f"preempt:{instance_id}:{now_utc():%Y%m%d%H%M}",
        target_id=instance_uuid,
        sms=True,
    )


_ANNOUNCEMENT_CHUNK = 1000


async def publish_announcement(
    session: AsyncSession, *, title: str, content: str, created_by: int, idempotency_key: str | None
) -> tuple[int, bool]:
    """Announcement broadcast: write the announcements row, then announcement notifications for
    every
    active user in chunked batches
    (dedup_key = ann:{announcement id}:{user_id}, on_conflict_do_nothing).
    Returns (reach count, created); created=False = idempotent replay.
    """
    if idempotency_key:
        existing = await find_replay(
            session, Announcement, owner_col=None, owner_id=None, key=idempotency_key
        )
        if existing is not None:
            return existing.reached, False

    announcement = Announcement(
        title=title,
        content=content,
        created_by=created_by,
        reached=0,
        idempotency_key=idempotency_key,
    )
    result = await insert_idempotent(
        session,
        announcement,
        model=Announcement,
        owner_col=None,
        owner_id=None,
        key=idempotency_key,
    )
    if result is not announcement:
        return result.reached, False
    user_ids = await list_active_user_ids(session)
    announcement.reached = len(user_ids)
    for i in range(0, len(user_ids), _ANNOUNCEMENT_CHUNK):
        await session.execute(
            pg_insert(Notification)
            .values(
                [
                    {
                        "user_id": uid,
                        "type": "announcement",
                        "title": title,
                        "content": content,
                        "severity": "info",
                        "dedup_key": f"ann:{announcement.id}:{uid}",
                    }
                    for uid in user_ids[i : i + _ANNOUNCEMENT_CHUNK]
                ]
            )
            .on_conflict_do_nothing(index_elements=["dedup_key"])
        )
    await session.commit()
    logger.info("announcement_published", title=title, reached=len(user_ids))
    return len(user_ids), True


async def admin_list_announcements(session: AsyncSession) -> list[Announcement]:
    """Announcement history (fixed cap, newest first)."""
    return list(
        (
            await session.execute(
                select(Announcement).order_by(Announcement.id.desc()).limit(ADMIN_LIST_CAP)
            )
        ).scalars()
    )


async def revoke_announcement(
    session: AsyncSession, announcement_id: int, *, revoked_by: int, reason: str
) -> Announcement:
    """Withdraw an announcement (under the row lock): the announcement becomes revoked and the
    fan-out notifications are revoked in the same transaction; repeating is 409."""
    announcement = await session.get(Announcement, announcement_id, with_for_update=True)
    if announcement is None:
        raise not_found()
    if announcement.status != "published":
        raise conflict(key="adminapi.announcementAlreadyRevoked")
    announcement.status = "revoked"
    announcement.revoked_by = revoked_by
    announcement.revoked_at = now_utc()
    announcement.revoke_reason = reason
    await session.execute(
        update(Notification)
        .where(
            Notification.type == "announcement",
            Notification.dedup_key.like(f"ann:{announcement.id}:%"),
            Notification.status == "published",
        )
        .values(status="revoked")
    )
    await session.commit()
    await session.refresh(announcement)
    logger.info("announcement_revoked", announcement_id=announcement.id, revoked_by=revoked_by)
    return announcement


async def list_notifications(
    session: AsyncSession,
    user_id: int,
    *,
    unread_only: bool = False,
    cursor: str | None = None,
    limit: int | None = None,
) -> "Page[NotificationOut]":
    """Notification list: descending cursor pagination, published only."""
    stmt = (
        select(Notification)
        .where(Notification.user_id == user_id, Notification.status == "published")
        .order_by(Notification.id.desc())
    )
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    page_items, next_cursor = await paginate_by_id(
        session, stmt, id_col=Notification.id, cursor=cursor, limit=limit
    )
    return Page[NotificationOut](
        items=[NotificationOut.model_validate(r) for r in page_items], next_cursor=next_cursor
    )


async def unread_count(session: AsyncSession, user_id: int) -> int:
    """Unread notification count (top-bar badge)."""
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.user_id == user_id,
                    Notification.status == "published",
                    Notification.read_at.is_(None),
                )
            )
        ).scalar_one()
    )


async def mark_read(session: AsyncSession, user_id: int, notification_id: int) -> None:
    row = await session.get(Notification, notification_id)
    if row is not None and row.user_id == user_id and row.read_at is None:
        row.read_at = now_utc()
        await session.commit()


async def mark_all_read(session: AsyncSession, user_id: int) -> None:
    """Mark all read (idempotent)."""
    await session.execute(
        update(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.status == "published",
            Notification.read_at.is_(None),
        )
        .values(read_at=now_utc())
    )
    await session.commit()


ALERT_STREAM_TYPES = ("admin_alert", "gpu_fault")


async def admin_alert_stream(
    session: AsyncSession,
    *,
    severity: str | None = None,
    alert_type: str | None = None,
    acked: bool | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> RawPage[Notification]:
    """Admin alert feed (platform-level + per-tenant gpu_fault): cursor pagination by id descending.

    The severity / type / ack filters all run in the database, never on the page the frontend
    already
    fetched.
    """
    stmt = (
        select(Notification)
        .where(Notification.type.in_(ALERT_STREAM_TYPES))
        .order_by(Notification.id.desc())
    )
    if severity:
        stmt = stmt.where(Notification.severity == severity)
    if alert_type:
        stmt = stmt.where(Notification.type == alert_type)
    if acked is not None:
        stmt = stmt.where(
            Notification.acked_at.is_not(None) if acked else Notification.acked_at.is_(None)
        )
    page_items, next_cursor = await paginate_by_id(
        session, stmt, id_col=Notification.id, cursor=cursor, limit=limit
    )
    return RawPage(items=page_items, next_cursor=next_cursor)


async def ack_admin_alert(session: AsyncSession, alert_id: int, *, acked_by: int) -> Notification:
    """Acknowledge an alert (under the row lock): record who and when. Rows outside the alert feed →
    404; repeating → 409."""
    row = await session.get(Notification, alert_id, with_for_update=True)
    if row is None or row.type not in ALERT_STREAM_TYPES:
        raise not_found()
    if row.acked_at is not None:
        raise conflict(key="adminapi.alertAlreadyAcked")
    row.acked_by = acked_by
    row.acked_at = now_utc()
    await session.commit()
    await session.refresh(row)
    logger.info("admin_alert_acked", alert_id=row.id, acked_by=acked_by)
    return row


async def unread_alert_count(session: AsyncSession) -> tuple[int, int]:
    """Unacknowledged alert count (top-bar bell badge): (total, of which critical)."""
    row = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(Notification.severity == "critical"),
            ).where(Notification.type.in_(ALERT_STREAM_TYPES), Notification.acked_at.is_(None))
        )
    ).one()
    return int(row[0]), int(row[1])


async def ingest_alertmanager(session: AsyncSession, payload: dict) -> int:
    """Ingest alerts deduplicated by fingerprint+startsAt, write notifications and the SMS outbox in
    one transaction, then commit.

    critical platform alerts notify the on-call phone; GPU alerts map only to verified active
    tenants
    and are rate-limited per user.
    """
    cfg = await get_runtime_config(session)
    oncall_phone = cfg.oncall_phone
    written = 0
    for alert in payload.get("alerts", []):
        fingerprint = alert.get("fingerprint", "")
        starts_at = alert.get("startsAt", "")
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
        severity = labels.get("severity", "warning")
        alertname = labels.get("alertname", "unknown")
        summary = annotations.get("summary") or annotations.get("description") or alertname
        dedup = f"am:{fingerprint}:{starts_at}"
        node_name = labels.get("hostname") or None

        ok = await notify(
            session,
            None,
            type_="admin_alert",
            title=alertname,
            content=summary,
            severity="critical" if severity == "critical" else "warning",
            dedup_key=dedup,
            target_id=node_name,
            target_kind="node" if node_name else None,
        )
        if ok:
            written += 1
            if severity == "critical" and oncall_phone:
                enqueue(
                    session,
                    SMS_TASK_TYPE,
                    {
                        "phone": oncall_phone,
                        "title": server_copy("notify.oncall_sms.title", alertname=alertname),
                    },
                )

        ns = labels.get("namespace", "")
        prefix = get_settings().k8s_namespace_prefix
        if alertname.startswith("GPU") and ns.startswith(prefix):
            try:
                user_id = int(ns.removeprefix(prefix))
            except ValueError:
                continue
            if not await account_service.is_active_user(session, user_id):
                continue
            try:
                await check_rate_limit(
                    f"am-gpu-tenant:{user_id}", max_attempts=5, window_seconds=3600.0
                )
            except AppError:
                continue
            await notify(
                session,
                user_id,
                type_="gpu_fault",
                title=server_copy("notify.gpu_fault.title"),
                content=server_copy("notify.gpu_fault.content"),
                severity="critical",
                dedup_key=f"{dedup}:tenant:{user_id}",
                target_id=str(user_id),
                target_kind="tenant",
                sms=True,
            )
    await session.commit()
    return written
