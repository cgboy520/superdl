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


async def on_instance_transition(
    session: AsyncSession, instance: "Instance", event: "InstanceEvent"
) -> None:
    if event.from_status != RUNNING:
        return
    at = ensure_utc(event.created_at)
    charged = await settle_instance_window(
        session,
        instance_id=instance.id,
        user_id=instance.user_id,
        unit_price=instance.price_hourly,
        gpu_count=instance.gpu_count,
        window_start=hour_floor(at),
        window_end=at,
        source="tail",
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
