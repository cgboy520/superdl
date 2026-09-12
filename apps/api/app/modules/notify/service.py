"""通知服务:站内信 + 短信(outbox 异步)+ 告警接入;同类型预警 24h 去重(dedup_key 唯一约束)。"""

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
from app.core.outbox import OutboxTask, enqueue, outbox_handler
from app.core.pagination import Page, paginate_by_id
from app.core.platform_config import get_runtime_config
from app.core.ratelimit import check_rate_limit
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
    """写站内信(可选短信,同事务 enqueue notify.sms)。dedup_key 冲突返回 False。不 commit。
    target_id:跳转目标(instance 类 = 实例 uuid,ticket 类 = 工单 id),无目标留空;
    target_kind 只给管理端告警流(tenant / node / ticket),决定深链去向。
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


@outbox_handler(SMS_TASK_TYPE)
async def handle_notify_sms(session: AsyncSession, task: OutboxTask) -> None:
    """通知短信发送(outbox 执行,尽力而为,at-least-once)。
    收件人两形态:{"user_id": N} 或 {"phone": "1xx"}。
    """
    phone: str | None = task.payload.get("phone")
    user_id = task.payload.get("user_id")
    if phone is None and user_id is not None:
        try:
            user = await get_user(session, user_id)
        except AppError:
            logger.warning("sms_user_missing", user_id=user_id, task_id=task.id)
            return  # 用户不存在:无重试价值,直接消化
        phone = user.phone
    if not phone:
        logger.warning("sms_no_recipient", task_id=task.id)
        return  # 无收件人:配置错误,无重试价值
    cfg = await get_runtime_config(session)
    channel = await get_sms_channel(session)
    try:
        await ensure_sms_platform_quota()
    except AppError:
        # 平台预算池耗尽(RATE_LIMITED):消化不重试
        logger.warning("sms_platform_quota_exhausted", task_id=task.id)
        return
    await channel.send(phone, cfg.sms_template_notice or "", {"title": task.payload["title"]})


async def send_low_balance_warning(
    session: AsyncSession, user_id: int, *, est_hours: float, balance: str
) -> None:
    """余额预警(24h 同类去重),站内信 + 短信。"""
    await notify(
        session,
        user_id,
        type_="balance_warn",
        title="余额不足预警",
        content=f"当前余额 ¥{balance},按现有实例预计仅可再运行约 {est_hours:.1f} 小时,请及时充值。",
        severity="warning",
        dedup_key=f"balance_warn:{user_id}:{_day_bucket(now_utc())}",
        sms=True,
    )
    await session.commit()


async def send_arrears_notice(
    session: AsyncSession, user_id: int, *, action: str, detail: str
) -> None:
    titles = {
        "auto_stop": "余额耗尽,实例已自动关机",
        "freeze": "实例已冻结",
        "reclaim": "实例已回收",
    }
    await notify(
        session,
        user_id,
        type_="arrears",
        title=titles.get(action, "欠费通知"),
        content=detail,
        severity="warning",
        dedup_key=f"arrears:{action}:{user_id}:{_day_bucket(now_utc())}",
        sms=True,
    )
    # 由调用方 commit


async def send_subscription_notice(
    session: AsyncSession,
    user_id: int,
    *,
    action: str,
    detail: str,
    dedup_suffix: str,
    target_id: str | None = None,
) -> None:
    """包周期到期链路通知(站内信 + 短信);dedup_key 带 subscription/instance id + 日桶。"""
    titles = {
        "expiring": "包周期即将到期",
        "expired": "包周期已到期,实例已停机",
        "renewed": "包周期已自动续费",
        "renew_failed": "自动续费失败",
    }
    await notify(
        session,
        user_id,
        type_="subscription",
        title=titles.get(action, "包周期通知"),
        content=detail,
        severity="info" if action == "renewed" else "warning",
        dedup_key=f"subscription:{action}:{dedup_suffix}:{_day_bucket(now_utc())}",
        target_id=target_id,
        sms=True,
    )
    # 由调用方 commit


async def send_preemption_notice(
    session: AsyncSession,
    user_id: int,
    *,
    instance_name: str,
    grace_seconds: int,
    instance_id: int,
    instance_uuid: str,
) -> None:
    """竞价实例被抢占的通知(站内信 + 短信);dedup_key = 实例 id + 分钟位,不按天分桶。"""
    await notify(
        session,
        user_id,
        type_="preempted",
        title="竞价实例即将被回收",
        content=(
            f"{instance_name} 因平台需要容量将在 {grace_seconds} 秒后关机。"
            f"实例盘保留,有容量时可自行开机;已运行时长按实际秒数结算。"
        ),
        severity="warning",
        dedup_key=f"preempt:{instance_id}:{now_utc():%Y%m%d%H%M}",
        target_id=instance_uuid,
        sms=True,
    )


# 群发的单语句行数上限(PG 单条语句 65535 个绑定参数)
_ANNOUNCEMENT_CHUNK = 1000


async def publish_announcement(
    session: AsyncSession, *, title: str, content: str, created_by: int, idempotency_key: str | None
) -> tuple[int, bool]:
    """公告群发:落 announcements 行,再对全部 active 用户分块批量写 announcement 站内信
    (dedup_key = ann:{公告id}:{user_id},on_conflict_do_nothing)。
    返回 (触达人数, created);created=False = 幂等重放。
    """
    if idempotency_key:
        existing = await find_replay(
            session, Announcement, owner_col=None, owner_id=None, key=idempotency_key
        )
        if existing is not None:
            return existing.reached, False  # 幂等重放

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
        return result.reached, False  # 同键并发:返回胜出方的公告
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
    """公告历史(固定截断,最新在前)。"""
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
    """撤回公告(行锁内):公告置 revoked,同事务把 fanout 站内信置 revoked;重复撤回 409。"""
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
    """站内信列表:降序游标分页,只读 published。"""
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
    """未读站内信条数(顶栏角标)。"""
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
    """全部已读(幂等)。"""
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


# 管理端告警流(admin_alerts)覆盖的通知类型:平台级告警 + 映射到租户的 GPU 故障
ALERT_STREAM_TYPES = ("admin_alert", "gpu_fault")


async def admin_alert_stream(
    session: AsyncSession, *, severity: str | None = None
) -> list[Notification]:
    """管理端告警流(平台级 + 各租户 gpu_fault),最近 50 条;severity 可选精确过滤。"""
    stmt = (
        select(Notification)
        .where(Notification.type.in_(ALERT_STREAM_TYPES))
        .order_by(Notification.id.desc())
        .limit(50)
    )
    if severity:
        stmt = stmt.where(Notification.severity == severity)
    return list((await session.execute(stmt)).scalars())


async def ack_admin_alert(session: AsyncSession, alert_id: int, *, acked_by: int) -> Notification:
    """确认告警(行锁内):落确认人/时间。非告警流行 404;重复确认 409。"""
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
    """未确认告警计数(顶栏铃铛角标):(总数, 其中 critical)。"""
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
    """Alertmanager webhook:按 fingerprint+startsAt 幂等;GPU 告警映射到受影响租户;
    critical 平台告警短信直发值班手机(oncall_phone),同 dedup_key 幂等。
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
        # 节点维告警(DCGM 等带小写 hostname 标签)深链到节点页
        node_name = labels.get("hostname") or None

        # 平台级告警流
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
                    {"phone": oncall_phone, "title": f"[平台critical]{alertname}"},
                )

        # GPU 故障映射受影响租户(namespace=tenant-N)
        ns = labels.get("namespace", "")
        prefix = get_settings().k8s_namespace_prefix
        if alertname.startswith("GPU") and ns.startswith(prefix):
            try:
                user_id = int(ns.removeprefix(prefix))
            except ValueError:
                continue
            # namespace 归属查库核实(存在且活跃),再按用户限流

            if not await account_service.is_active_user(session, user_id):
                continue
            try:
                await check_rate_limit(
                    f"am-gpu-tenant:{user_id}", max_attempts=5, window_seconds=3600.0
                )
            except AppError:
                continue  # 该用户此路径已超频:跳过(平台告警流在上方已落,不受影响)
            await notify(
                session,
                user_id,
                type_="gpu_fault",
                title="GPU 硬件告警",
                content=(
                    "该实例所在 GPU 触发硬件故障告警,平台正在处理。"
                    "若实例因此停机,将按停机瞬间结算,之后不再计费。"
                ),
                severity="critical",
                dedup_key=f"{dedup}:tenant:{user_id}",
                target_id=str(user_id),
                target_kind="tenant",
                sms=True,
            )
    await session.commit()
    return written
