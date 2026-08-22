"""通知服务:站内信 + 短信(outbox 异步发送)+ 告警接入。

同类型预警 24h 去重(dedup_key 唯一约束,幂等)。
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.outbox import OutboxTask, enqueue, outbox_handler
from app.core.platform_config import get_effective_platform_config
from app.core.sms import get_sms_channel
from app.core.timeutil import now_utc
from app.modules.notify.models import Notification

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
    """
    from app.modules.account.service import get_user

    user_id = task.payload["user_id"]
    try:
        user = await get_user(session, user_id)
    except AppError:
        logger.warning("sms_user_missing", user_id=user_id, task_id=task.id)
        return  # 用户不存在:无重试价值,直接消化
    cfg = await get_effective_platform_config(session)
    channel = await get_sms_channel(session)
    await channel.send(
        user.phone, cfg["sms_template_notice"] or "", {"title": task.payload["title"]}
    )


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


# 群发的单语句行数上限:PG 单条语句 65535 个绑定参数,按 4 列 × 1000 行留足余量
_ANNOUNCEMENT_CHUNK = 1000


async def publish_announcement(session: AsyncSession, *, title: str, content: str) -> int:
    """公告群发:对全部 active 用户写 announcement 站内信。返回触达人数。

    分块批量 INSERT(替代逐用户 INSERT...RETURNING 的 N+1):公告无 dedup_key、
    允许重复发布,无需逐行冲突判定;单事务内仅 ⌈N/1000⌉ 条语句。
    """
    from app.modules.account.service import list_active_user_ids

    user_ids = await list_active_user_ids(session)
    for i in range(0, len(user_ids), _ANNOUNCEMENT_CHUNK):
        await session.execute(
            pg_insert(Notification).values(
                [
                    {
                        "user_id": uid,
                        "type": "announcement",
                        "title": title,
                        "content": content,
                        "severity": "info",
                    }
                    for uid in user_ids[i : i + _ANNOUNCEMENT_CHUNK]
                ]
            )
        )
    await session.commit()
    logger.info("announcement_published", title=title, reached=len(user_ids))
    return len(user_ids)


async def list_notifications(
    session: AsyncSession,
    user_id: int,
    *,
    unread_only: bool = False,
    cursor: str | None = None,
    limit: int | None = None,
):
    """站内信列表:降序(最新在前)游标分页,与流水/账单同一套分页语义。"""
    from app.core.pagination import Page, clamp_limit, decode_cursor_int, encode_cursor
    from app.modules.notify.schemas import NotificationOut

    lim = clamp_limit(limit)
    stmt = (
        select(Notification)
        .where(Notification.user_id == user_id)
        .order_by(Notification.id.desc())
        .limit(lim + 1)
    )
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    last_id = decode_cursor_int(cursor)
    if last_id is not None:
        stmt = stmt.where(Notification.id < last_id)
    rows = list((await session.execute(stmt)).scalars())
    next_cursor = encode_cursor(rows[lim - 1].id) if len(rows) > lim else None
    return Page[NotificationOut](
        items=[NotificationOut.model_validate(r) for r in rows[:lim]], next_cursor=next_cursor
    )


async def mark_read(session: AsyncSession, user_id: int, notification_id: int) -> None:
    row = await session.get(Notification, notification_id)
    if row is not None and row.user_id == user_id and row.read_at is None:
        row.read_at = now_utc()
        await session.commit()


async def admin_alert_stream(session: AsyncSession, limit: int = 50) -> list[Notification]:
    """管理端告警流(平台级 + 各租户 gpu_fault)。"""
    return list(
        (
            await session.execute(
                select(Notification)
                .where(Notification.type.in_(("admin_alert", "gpu_fault")))
                .order_by(Notification.id.desc())
                .limit(limit)
            )
        ).scalars()
    )


async def ingest_alertmanager(session: AsyncSession, payload: dict) -> int:
    """Alertmanager webhook:按 fingerprint+startsAt 幂等;GPU 告警映射到受影响租户。"""
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
