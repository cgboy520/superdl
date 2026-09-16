"""Spot preemption: reclaim spot instances to make room when an on-demand / subscription request
lacks capacity.
Select only within the same pool and model; by `created_at DESC`; nothing moves unless one full
instance can be freed.
Same transaction as the caller's instance creation; the state machine moves to stopping at once,
`instance.stop` runs after `spot_grace_seconds`.
"""

import math
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.gpu_models import canonical_gpu_model
from app.core.logging import get_logger
from app.core.metrics import SPOT_PREEMPTED_TOTAL
from app.core.outbox import enqueue
from app.core.pricing import MARKET_SPOT
from app.modules.notify import service as notify_service
from app.modules.orchestrator import statemachine as sm_def
from app.modules.orchestrator.models import Instance
from app.modules.orchestrator.transitions import transition

if TYPE_CHECKING:
    from app.modules.catalog.models import Sku

logger = get_logger(__name__)

REASON_PREEMPTED = "preempted"


async def pick_victims(
    session: AsyncSession,
    *,
    pool_label: str,
    gpu_model_selector: str | None,
    need_cards: int,
) -> list[Instance]:
    """Pick spot instances freeing `need_cards` cards (newest first); an empty list when not enough.
    Each instance counts about gpu_count cards."""
    if need_cards <= 0:
        return []
    rows = list(
        (
            await session.execute(
                select(Instance)
                .where(
                    Instance.status == sm_def.RUNNING,
                    Instance.market == MARKET_SPOT,
                    Instance.spec["pool_label"].astext == pool_label,
                    Instance.spec["gpu_model_selector"].astext.is_not_distinct_from(
                        gpu_model_selector
                    ),
                )
                .order_by(Instance.created_at.desc(), Instance.id.desc())
            )
        ).scalars()
    )
    picked: list[Instance] = []
    freed = 0
    for inst in rows:
        picked.append(inst)
        freed += max(1, inst.gpu_count)
        if freed >= need_cards:
            return picked
    return []


def cards_needed(*, deficit_slots: int, slots_per_card: int) -> int:
    """Convert "missing slots of this SKU" into "missing cards". Slot definition in
    catalog.sellable_per_gpu."""
    if deficit_slots <= 0:
        return 0
    return math.ceil(deficit_slots / max(1, slots_per_card))


async def preempt(
    session: AsyncSession,
    victims: list[Instance],
    *,
    grace_seconds: int,
    requested_by: int,
    admin_reason: str | None = None,
) -> None:
    """Reclaim the selected spot instances without commit (same transaction as the caller's
    creation); moving to stopping posts the tail bill, the grace window is not billed."""
    for inst in victims:
        await transition(
            session,
            inst,
            sm_def.STOPPING,
            reason=REASON_PREEMPTED,
            actor="system",
            metadata={
                "grace_seconds": grace_seconds,
                "requested_by": requested_by,
                **({"admin_reason": admin_reason} if admin_reason else {}),
            },
        )
        enqueue(
            session,
            "instance.stop",
            {"instance_id": inst.id},
            delay_seconds=grace_seconds,
        )
        await notify_service.send_preemption_notice(
            session,
            inst.user_id,
            instance_name=inst.name,
            grace_seconds=grace_seconds,
            instance_id=inst.id,
            instance_uuid=inst.uuid,
        )
        SPOT_PREEMPTED_TOTAL.inc()
        logger.warning(
            "spot_instance_preempted",
            instance_id=inst.id,
            user_id=inst.user_id,
            gpu_count=inst.gpu_count,
            grace_seconds=grace_seconds,
            requested_by=requested_by,
        )


async def try_free_capacity(
    session: AsyncSession,
    *,
    sku: "Sku",
    deficit_slots: int,
    slots_per_card: int,
    grace_seconds: int,
    requested_by: int,
) -> bool:
    """Try to close the gap by preemption; True when room was made (reclamation issued in this
    transaction), otherwise False."""
    need = cards_needed(deficit_slots=deficit_slots, slots_per_card=slots_per_card)
    victims = await pick_victims(
        session,
        pool_label=sku.pool_label,
        gpu_model_selector=canonical_gpu_model(sku.gpu_model),
        need_cards=need,
    )
    if not victims:
        return False
    await preempt(session, victims, grace_seconds=grace_seconds, requested_by=requested_by)
    return True
