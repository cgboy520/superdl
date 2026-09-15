"""计费边监听器:实例离开 running 时与状态迁移同事务出尾账(wire_modules() 注册)。"""

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.pricing import MARKET_SUBSCRIPTION
from app.core.timeutil import ensure_utc, hour_floor
from app.modules.billing import subscriptions
from app.modules.billing.settlement import settle_instance_window, truncated_at
from app.modules.orchestrator import statemachine as sm_def
from app.modules.orchestrator.transitions import register_transition_listener

if TYPE_CHECKING:
    from app.modules.orchestrator.models import Instance, InstanceEvent

logger = get_logger(__name__)


async def on_instance_transition(
    session: AsyncSession, instance: "Instance", event: "InstanceEvent"
) -> None:
    if event.to_status == sm_def.RELEASING and instance.market == MARKET_SUBSCRIPTION:
        await subscriptions.cancel_for_instance(session, instance.id)
    if event.from_status != sm_def.RUNNING:
        return
    if instance.market == MARKET_SUBSCRIPTION:
        return
    edge_at = ensure_utc(event.created_at)
    at = truncated_at(edge_at, event.from_status, event.event_metadata)
    detail_extra = (
        {"truncated_at": at.isoformat(), "truncate_reason": event.reason} if at < edge_at else None
    )
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
