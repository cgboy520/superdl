"""通知服务:站内信 + 短信(outbox 异步发送)+ 告警接入。

同类型预警 24h 去重(dedup_key 唯一约束,幂等)。
"""

from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode, not_found
from app.core.logging import get_logger
from app.core.outbox import OutboxTask, enqueue, outbox_handler
from app.core.platform_config import get_effective_platform_config
from app.core.sms import get_sms_channel
from app.core.timeutil import now_utc
from app.modules.notify.models import Announcement, Notification

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
    sms: bool = False,
) -> bool:
    """写站内信(可选发短信)。dedup_key 冲突 = 已通知过,返回 False。不 commit。

    短信不在本事务里发:同事务 enqueue 一条 notify.sms,由 outbox worker 异步投递
    (渠道网络调用可能秒级,持锁/持连接期间调外部渠道会放大故障面)。
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
    """通知短信发送(outbox 执行)。站内信已落库,短信尽力而为:失败退避重试,超预算进死信。

    幂等说明:SMS 通道侧无法去重,at-least-once 下同一通知可能投递多条短信,接受。
    收件人两形态:{"user_id": N}(站内用户)或 {"phone": "1xx"}(值班手机等直发)。
    """
    from app.modules.account.service import get_user

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
    cfg = await get_effective_platform_config(session)
    channel = await get_sms_channel(session)
    await channel.send(phone, cfg["sms_template_notice"] or "", {"title": task.payload["title"]})


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
    # patrol 的事务里调用,由调用方 commit;此处不强制


# 群发的单语句行数上限:PG 单条语句 65535 个绑定参数,按 5 列 × 1000 行留足余量
_ANNOUNCEMENT_CHUNK = 1000

# 管理端公告历史固定截断,与 admin/components/ListCapNote.tsx 的 LIST_CAPS.announcements 对齐
ANNOUNCEMENT_LIST_CAP = 200


async def publish_announcement(
    session: AsyncSession, *, title: str, content: str, created_by: int
) -> int:
    """公告群发:先落 announcements 记录,再对全部 active 用户写 announcement 站内信。
    返回触达人数。

    fanout 行 dedup_key = ann:{公告id}:{user_id}:撤回按前缀精确收回,
    且发布中途失败重试逐用户幂等(on_conflict_do_nothing)。分块批量 INSERT
    替代逐用户 INSERT 的 N+1;单事务内仅 ⌈N/1000⌉ 条语句。
    """
    from app.modules.account.service import list_active_user_ids

    announcement = Announcement(title=title, content=content, created_by=created_by, reached=0)
    session.add(announcement)
    await session.flush()  # 先取公告 id:fanout dedup_key 以其为前缀
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
    return len(user_ids)


async def admin_list_announcements(session: AsyncSession) -> list[Announcement]:
    """公告历史(固定截断,最新在前)。"""
    return list(
        (
            await session.execute(
                select(Announcement).order_by(Announcement.id.desc()).limit(ANNOUNCEMENT_LIST_CAP)
            )
        ).scalars()
    )


async def revoke_announcement(
    session: AsyncSession, announcement_id: int, *, revoked_by: int, reason: str
) -> Announcement:
    """撤回公告(行锁内状态迁移):公告置 revoked,同事务把 fanout 站内信全部收回
    (status=revoked,用户端列表只读 published,即对全部租户不可见)。重复撤回 → 409。
    """
    announcement = await session.get(Announcement, announcement_id, with_for_update=True)
    if announcement is None:
        raise not_found()
    if announcement.status != "published":
        raise AppError(
            ErrorCode.CONFLICT,
            key="adminapi.announcementAlreadyRevoked",
            http_status=409,
        )
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
):
    """站内信列表:降序(最新在前)游标分页,与流水/账单同一套分页语义。
    只读 published:被撤回公告(status=revoked)对用户不可见。"""
    from app.core.pagination import Page, clamp_limit, decode_cursor_int, slice_page
    from app.modules.notify.schemas import NotificationOut

    lim = clamp_limit(limit)
    stmt = (
        select(Notification)
        .where(Notification.user_id == user_id, Notification.status == "published")
        .order_by(Notification.id.desc())
        .limit(lim + 1)
    )
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    last_id = decode_cursor_int(cursor)
    if last_id is not None:
        stmt = stmt.where(Notification.id < last_id)
    rows = list((await session.execute(stmt)).scalars())
    page_items, next_cursor = slice_page(rows, lim, key=lambda r: r.id)
    return Page[NotificationOut](
        items=[NotificationOut.model_validate(r) for r in page_items], next_cursor=next_cursor
    )


async def mark_read(session: AsyncSession, user_id: int, notification_id: int) -> None:
    row = await session.get(Notification, notification_id)
    if row is not None and row.user_id == user_id and row.read_at is None:
        row.read_at = now_utc()
        await session.commit()


async def mark_all_read(session: AsyncSession, user_id: int) -> int:
    """全部已读(幂等):返回本次新标记的条数(重复调用返回 0)。"""
    result = cast(
        CursorResult[Any],
        await session.execute(
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.status == "published",
                Notification.read_at.is_(None),
            )
            .values(read_at=now_utc())
        ),
    )
    await session.commit()
    return result.rowcount or 0


# 管理端告警流(admin_alerts)覆盖的通知类型:平台级告警 + 映射到租户的 GPU 故障
ALERT_STREAM_TYPES = ("admin_alert", "gpu_fault")


async def admin_alert_stream(
    session: AsyncSession, limit: int = 50, *, severity: str | None = None
) -> list[Notification]:
    """管理端告警流(平台级 + 各租户 gpu_fault)。severity 精确过滤(可选)。"""
    stmt = (
        select(Notification)
        .where(Notification.type.in_(ALERT_STREAM_TYPES))
        .order_by(Notification.id.desc())
        .limit(limit)
    )
    if severity:
        stmt = stmt.where(Notification.severity == severity)
    return list((await session.execute(stmt)).scalars())


def alert_link_target(row: Notification) -> tuple[str | None, str | None]:
    """告警跳转目标(从现有 type/user_id/title/dedup_key 派生):(kind, id)。

    - gpu_fault(带 user_id)→ tenant:受影响租户;
    - GPU 硬件类告警(title 为 alertname,GPU 前缀)→ node:节点页;
    - 工单联动告警(dedup_key 为 ticket:* 前缀)→ ticket:工单页;
    - 其余无 target,前端不可点。
    """
    if row.type == "gpu_fault" and row.user_id is not None:
        return "tenant", str(row.user_id)
    if row.dedup_key is not None and row.dedup_key.startswith("ticket"):
        return "ticket", None
    if row.title.startswith("GPU"):
        return "node", None
    return None, None


async def ack_admin_alert(session: AsyncSession, alert_id: int, *, acked_by: int) -> Notification:
    """确认告警(行锁内):落确认人/时间。非告警流行 404;重复确认 409。"""
    row = await session.get(Notification, alert_id, with_for_update=True)
    if row is None or row.type not in ALERT_STREAM_TYPES:
        raise not_found()
    if row.acked_at is not None:
        raise AppError(
            ErrorCode.CONFLICT,
            key="adminapi.alertAlreadyAcked",
            http_status=409,
        )
    row.acked_by = acked_by
    row.acked_at = now_utc()
    await session.commit()
    await session.refresh(row)
    logger.info("admin_alert_acked", alert_id=row.id, acked_by=acked_by)
    return row


async def unread_alert_count(session: AsyncSession) -> int:
    """未确认告警计数(顶栏铃铛角标口径:告警流中尚未 ack 的行数)。"""
    return (
        await session.execute(
            select(func.count()).where(
                Notification.type.in_(ALERT_STREAM_TYPES), Notification.acked_at.is_(None)
            )
        )
    ).scalar_one()


async def ingest_alertmanager(session: AsyncSession, payload: dict) -> int:
    """Alertmanager webhook:按 fingerprint+startsAt 幂等;GPU 告警映射到受影响租户。

    critical 平台告警额外短信直发值班手机(平台配置 oncall_phone):
    平台自身故障时站内信流可能无人刷页面,触达通道不得依赖平台自身可用性。
    短信随站内信同一 dedup_key 幂等:重复 firing 不重复发短信。
    """
    cfg = await get_effective_platform_config(session)
    oncall_phone = cfg.get("oncall_phone", "")
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

        # 平台级告警流
        ok = await notify(
            session,
            None,
            type_="admin_alert",
            title=alertname,
            content=summary,
            severity="critical" if severity == "critical" else "warning",
            dedup_key=dedup,
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
                sms=True,
            )
    await session.commit()
    return written
