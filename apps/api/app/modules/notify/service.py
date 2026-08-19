"""通知服务:站内信 + 短信(mock 落日志,真实渠道人工事项 #6 后接入)+ 告警接入。

同类型预警 24h 去重(dedup_key 唯一约束,幂等)。
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
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
    """写站内信(可选发短信)。dedup_key 冲突 = 已通知过,返回 False。不 commit。"""
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
        await _send_sms_notice(session, user_id, title)
    return True


async def _send_sms_notice(session: AsyncSession, user_id: int, title: str) -> None:
    """通知短信:尽力而为,失败仅记日志(站内信已落库,不因渠道故障中断业务事务)。"""
    from app.modules.account.service import get_user

    try:
        user = await get_user(session, user_id)
        settings = get_settings()
        await get_sms_channel().send(
            user.phone, settings.sms_template_notice or "", {"title": title}
        )
    except Exception as exc:
        logger.warning("sms_notify_failed", user_id=user_id, error=str(exc))


async def send_low_balance_warning(
    session: AsyncSession, user_id: int, *, est_hours: float, balance: str
) -> None:
    """余额预警(24h 同类去重),站内信 + 短信。"""
    await notify(
        session,
        user_id,
        type_="balance_warn",
        title="余额不足预警",
        content=(
            f"当前余额 ¥{balance},按现有实例预计仅可再运行约 {est_hours:.1f} 小时。"
            "余额耗尽后实例将自动关机并进入冻结倒计时,请及时充值。"
        ),
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


async def publish_announcement(session: AsyncSession, *, title: str, content: str) -> int:
    """公告群发:对全部 active 用户写 announcement 站内信。返回触达人数。

    MVP 规模直接逐用户落行;用户量上来后改 outbox 任务分批。
    """
    from app.modules.account.service import list_active_user_ids

    user_ids = await list_active_user_ids(session)
    for uid in user_ids:
        await notify(
            session,
            uid,
            type_="announcement",
            title=title,
            content=content,
            severity="info",
        )
    await session.commit()
    logger.info("announcement_published", title=title, reached=len(user_ids))
    return len(user_ids)


async def list_notifications(
    session: AsyncSession, user_id: int, *, unread_only: bool = False, limit: int = 50
) -> list[Notification]:
    stmt = (
        select(Notification)
        .where(Notification.user_id == user_id)
        .order_by(Notification.id.desc())
        .limit(limit)
    )
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    return list((await session.execute(stmt)).scalars())


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
                    "您的实例所在 GPU 触发硬件告警,平台已介入处理;"
                    "若实例受影响将按停机结算并补偿代金券(见故障 SOP)。"
                ),
                severity="critical",
                dedup_key=f"{dedup}:tenant:{user_id}",
                sms=True,
            )
    await session.commit()
    return written
