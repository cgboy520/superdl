"""Billing edge listener: when an instance leaves running, the tail bill is posted in the same
transaction as the transition (registered by wire_modules());
a creating/starting→failed edge carrying occupied_since is billed for the actual occupied
stretch."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.pricing import MARKET_SUBSCRIPTION
from app.core.timeutil import ensure_utc, hour_floor
from app.modules.billing import subscriptions
from app.modules.billing.settlement import (
    correct_fault_bills,
    occupied_since,
    settle_instance_window,
    truncated_at,
)
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
    since = occupied_since(event.from_status, event.to_status, event.event_metadata)
    if event.from_status != sm_def.RUNNING and since is None:
        return
    edge_at = ensure_utc(event.created_at)
    at = truncated_at(edge_at, event.from_status, event.event_metadata)
    await _correct_trusted_fault(session, instance, event, at, edge_at)
    if instance.market == MARKET_SUBSCRIPTION:
        return
    if since is not None:
        charged = await _settle_occupancy(session, instance, since, edge_at, reason=event.reason)
    else:
        extra = (
            {"truncated_at": at.isoformat(), "truncate_reason": event.reason}
            if at < edge_at
            else None
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
            detail_extra=extra,
        )
    if charged > 0:
        logger.info(
            "tail_bill_charged",
            instance_id=instance.id,
            amount=str(charged),
            reason=event.reason,
        )


async def _correct_trusted_fault(
    session: AsyncSession,
    instance: "Instance",
    event: "InstanceEvent",
    cutoff: datetime,
    edge_at: datetime,
) -> None:
    """Only the system's platform-loss edge authorizes refunds, never ordinary settlement."""
    if (
        event.actor != "system"
        or event.reason not in ("node_lost", "pod_lost")
        or event.from_status != sm_def.RUNNING
        or event.to_status != sm_def.FAILED
        or cutoff >= edge_at
    ):
        return
    await session.flush()
    refunded = await correct_fault_bills(
        session,
        instance_id=instance.id,
        event_id=event.id,
        cutoff=cutoff,
        edge_at=edge_at,
        reason=event.reason,
    )
    if refunded > 0:
        logger.info(
            "fault_bills_refunded",
            instance_id=instance.id,
            event_id=event.id,
            amount=str(refunded),
        )


async def _settle_occupancy(
    session: AsyncSession,
    instance: "Instance",
    since: datetime,
    edge_at: datetime,
    *,
    reason: str,
) -> Decimal:
    """Bill [since, edge_at) per calendar hour (occupancy may span hours; earlier clock-hour
    settlement did not list this instance as a candidate)."""
    extra = {"occupied_since": since.isoformat(), "truncate_reason": reason}
    total = Decimal("0.00")
    cursor = hour_floor(since)
    while cursor <= hour_floor(edge_at):
        window_end = min(cursor + timedelta(hours=1), edge_at)
        if window_end > cursor:
            total += await settle_instance_window(
                session,
                instance_id=instance.id,
                user_id=instance.user_id,
                unit_price=instance.price_hourly,
                gpu_count=instance.gpu_count,
                window_start=cursor,
                window_end=window_end,
                source="tail",
                detail_extra=extra,
            )
        cursor += timedelta(hours=1)
    return total


_registered = False


def register_billing_edge_listener() -> None:
    global _registered
    if _registered:
        return
    register_transition_listener(on_instance_transition)
    _registered = True
