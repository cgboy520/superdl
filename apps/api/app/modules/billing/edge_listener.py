"""计费边监听器:实例离开 running 时与状态迁移同事务出尾账(wire_modules() 注册)。"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.pricing import MARKET_SUBSCRIPTION
from app.core.timeutil import ensure_utc, hour_floor
from app.modules.billing import subscriptions
from app.modules.billing.settlement import settle_instance_window
from app.modules.orchestrator import statemachine as sm_def
from app.modules.orchestrator.transitions import register_transition_listener

if TYPE_CHECKING:
    from app.modules.orchestrator.models import Instance, InstanceEvent

logger = get_logger(__name__)

# 平台责任失联(node_lost/pod_lost):计费截断到 Pod 首次 not-ready 时刻。pod_unready 不在此列
_TRUNCATE_REASONS = ("node_lost", "pod_lost")


async def on_instance_transition(
    session: AsyncSession, instance: "Instance", event: "InstanceEvent"
) -> None:
    if event.to_status == sm_def.RELEASING and instance.market == MARKET_SUBSCRIPTION:
        # 中途释放不退款,订阅转 cancelled;挂在迁移监听器上覆盖用户释放/欠费回收/到期回收/强制回收
        await subscriptions.cancel_for_instance(session, instance.id)
    if event.from_status != sm_def.RUNNING:
        return
    if instance.market == MARKET_SUBSCRIPTION:
        # 包周期离开 running 不出尾账。三处配套过滤之一
        # (另两处:wallet.assert_can_afford、billing.patrol)
        return
    at = ensure_utc(event.created_at)
    detail_extra = None
    meta = event.event_metadata or {}
    if event.reason in _TRUNCATE_REASONS and meta.get("unready_since"):
        # 与 settlement._billing_view 同口径:截断到 Pod 首次 not-ready 时刻;
        # 本次退出边的 metadata 只有这里能读到
        unready_at = ensure_utc(datetime.fromisoformat(str(meta["unready_since"])))
        if unready_at < at:
            at = unready_at
            detail_extra = {"truncated_at": meta["unready_since"], "truncate_reason": event.reason}
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
    register_transition_listener(on_instance_transition)
    _registered = True
