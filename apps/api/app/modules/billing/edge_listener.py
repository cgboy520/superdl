"""计费边监听器:实例离开 running 的瞬间,与状态迁移同事务出尾账。

由 wire_modules() 注册到 orchestrator 的 transition 监听器,尾账与状态迁移原子提交。
"""

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.timeutil import ensure_utc, hour_floor
from app.modules.billing.settlement import settle_instance_window

if TYPE_CHECKING:
    from app.modules.orchestrator.models import Instance, InstanceEvent

logger = get_logger(__name__)

RUNNING = "running"

# 平台责任失联(节点失联/Pod 丢失):计费截断到 Pod 首次 not-ready 的时刻,
# 判定前的宽限观察期不向用户计费。pod_unready(节点正常,负载自身问题)不在此列。
_TRUNCATE_REASONS = ("node_lost", "pod_lost")


async def on_instance_transition(
    session: AsyncSession, instance: "Instance", event: "InstanceEvent"
) -> None:
    if event.from_status != RUNNING:
        return
    at = ensure_utc(event.created_at)
    detail_extra = None
    if event.reason in _TRUNCATE_REASONS and instance.unready_since is not None:
        unready_at = ensure_utc(instance.unready_since)
        # 健全性:早于本次进入 running 的 unready_since 是上次失联 episode 的残留,
        # 不参与截断——否则几天前的记录会把整段计费截断到过去时刻(本小时计 0 秒)
        from app.modules.orchestrator.service import last_entered_status_at

        entered_running = await last_entered_status_at(session, instance.id, RUNNING)
        stale = entered_running is not None and unready_at < ensure_utc(entered_running)
        if not stale and unready_at < at:
            at = unready_at
            # 截断依据留进 bills_hourly.detail(事件重建层另有同口径截断,见
            # settlement._billing_view:整点结算不会把宽限期秒数再补回来)
            detail_extra = {
                "truncated_at": unready_at.isoformat(),
                "truncate_reason": event.reason,
            }
    charged = await settle_instance_window(
        session,
        instance_id=instance.id,
        user_id=instance.user_id,
        unit_price=instance.price_hourly,
        gpu_count=instance.gpu_count,
        window_start=hour_floor(at),
        window_end=at,
        source="tail",
        detail_extra=detail_extra,
    )
    if charged > 0:
        logger.info(
            "tail_bill_charged",
            instance_id=instance.id,
            amount=str(charged),
            reason=event.reason,
        )


_registered = False


def register_billing_edge_listener() -> None:
    global _registered
    if _registered:
        return
    from app.modules.orchestrator.service import register_transition_listener

    register_transition_listener(on_instance_transition)
    _registered = True
