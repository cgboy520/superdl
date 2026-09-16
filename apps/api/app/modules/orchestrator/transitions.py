"""Instance state transitions and data-disk arrears chain primitives; event and outbox in the same
transaction, no commit."""

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import conflict
from app.core.logging import get_logger
from app.core.outbox import enqueue
from app.core.platform_config import get_runtime_config
from app.core.timeutil import now_utc
from app.modules.orchestrator import statemachine as sm_def
from app.modules.orchestrator.models import DataDisk, Instance, InstanceEvent
from app.modules.orchestrator.queries import DISK_ARREARS_CHAIN_STATUSES
from app.modules.orchestrator.statemachine import validate_transition

logger = get_logger(__name__)

TransitionListener = Callable[[AsyncSession, Instance, InstanceEvent], Awaitable[None]]
_transition_listeners: list[TransitionListener] = []


def register_transition_listener(listener: TransitionListener) -> None:
    _transition_listeners.append(listener)


async def transition(
    session: AsyncSession,
    instance: Instance,
    to_status: str,
    *,
    reason: str,
    actor: str,
    metadata: dict[str, Any] | None = None,
) -> InstanceEvent:
    """Validate + optimistic-lock update + event + listeners; no commit."""
    from_status = instance.status
    validate_transition(from_status, to_status)
    result = cast(
        CursorResult[Any],
        await session.execute(
            update(Instance)
            .where(Instance.id == instance.id, Instance.version == instance.version)
            .values(status=to_status, version=instance.version + 1)
        ),
    )
    if result.rowcount == 0:
        raise conflict(key="orchestrator.stateChangedRetry")
    instance.status = to_status
    instance.version += 1
    event = InstanceEvent(
        instance_id=instance.id,
        from_status=from_status,
        to_status=to_status,
        reason=reason,
        actor=actor,
        event_metadata=metadata,
        created_at=now_utc(),
    )
    session.add(event)
    await session.flush()
    for listener in _transition_listeners:
        await listener(session, instance, event)
    return event


async def system_stop(session: AsyncSession, instance: Instance, *, reason: str) -> None:
    """Platform-side stop; the caller supplies reason (arrears_stop / subscription_expired ...)."""
    await transition(session, instance, sm_def.STOPPING, reason=reason, actor="system")
    enqueue(session, "instance.stop", {"instance_id": instance.id})


async def freeze_instance(
    session: AsyncSession, instance: Instance, deadline: datetime, *, reason: str = "arrears_freeze"
) -> None:
    await transition(
        session,
        instance,
        sm_def.FROZEN,
        reason=reason,
        actor="system",
        metadata={"deadline": deadline.isoformat()},
    )
    instance.frozen_deadline = deadline


async def unfreeze_instance(session: AsyncSession, instance: Instance) -> None:
    await transition(session, instance, sm_def.STOPPED, reason="recharge_unfreeze", actor="system")
    instance.frozen_deadline = None


async def reclaim_frozen(session: AsyncSession, instance: Instance) -> None:
    await transition(session, instance, sm_def.RELEASING, reason="arrears_reclaim", actor="system")
    instance.frozen_deadline = None
    enqueue(session, "instance.release", {"instance_id": instance.id})


async def stop_all_for_user(session: AsyncSession, user_id: int, *, reason: str) -> int:
    """Stop every running instance of the user (for freezing), returning the count;
    creating/starting are caught later by billing.patrol."""
    rows = list(
        (
            await session.execute(
                select(Instance).where(
                    Instance.user_id == user_id, Instance.status == sm_def.RUNNING
                )
            )
        ).scalars()
    )
    for inst in rows:
        await transition(session, inst, sm_def.STOPPING, reason=reason, actor="admin")
        enqueue(session, "instance.stop", {"instance_id": inst.id})
    if rows:
        logger.warning("tenant_frozen_instances_stopped", user_id=user_id, count=len(rows))
    return len(rows)


SettleDiskPending = Callable[[AsyncSession, DataDisk], Awaitable[object]]


def _frozen_clock_forgiven(disk: DataDisk, now: datetime, frozen_days: int) -> bool:
    """The frozen-deletion clock resets only after staying in good standing for more than
    disk_frozen_days after payment; otherwise the next freeze continues the old clock."""
    return disk.grace_ended_at is not None and now - disk.grace_ended_at > timedelta(
        days=frozen_days
    )


def _recover_disk(disk: DataDisk, now: datetime) -> bool:
    """Payment: grace / frozen → active, grace_ended_at recorded; frozen_started_at kept."""
    if disk.status not in ("grace", "frozen"):
        return False
    disk.status = "active"
    disk.grace_ended_at = now
    return True


async def _advance_disk_arrears(
    session: AsyncSession,
    disk: DataDisk,
    now: datetime,
    *,
    grace_days: int,
    frozen_days: int,
    settle_pending: SettleDiskPending,
) -> bool:
    """Arrears: active → grace → frozen → deleting; the grace clock never resets, the frozen clock
    restarts only after it was reset."""
    if disk.status == "active":
        await settle_pending(session, disk)
        disk.status = "grace"
        if disk.grace_started_at is None:
            disk.grace_started_at = now
        if _frozen_clock_forgiven(disk, now, frozen_days):
            disk.frozen_started_at = None
        disk.grace_ended_at = None
        return True
    if disk.status == "grace" and disk.grace_started_at is not None:
        if now - disk.grace_started_at <= timedelta(days=grace_days):
            return False
        disk.status = "frozen"
        if disk.frozen_started_at is None:
            disk.frozen_started_at = now
        return True
    if disk.status == "frozen" and disk.frozen_started_at is not None:
        if now - disk.frozen_started_at <= timedelta(days=frozen_days):
            return False
        disk.status = "deleting"
        enqueue(session, "disk.deprovision", {"disk_id": disk.id})
        logger.warning("disk_arrears_wipe_scheduled", disk_id=disk.id)
        return True
    return False


async def arrears_transition_disks(
    session: AsyncSession, user_id: int, in_arrears: bool, *, settle_pending: SettleDiskPending
) -> int:
    """Arrears patrol hook: advance / roll back the user's data-disk arrears chain by available
    balance, returning the change count.
    grace_started_at is not reset once grace was entered; frozen_started_at is not reset by payment
    and the next freeze continues the old clock,
    only staying in good standing for more than disk_frozen_days after payment resets it.
    """
    policies = await get_runtime_config(session)
    now = now_utc()
    changed = 0
    disks = list(
        (
            await session.execute(
                select(DataDisk).where(
                    DataDisk.user_id == user_id,
                    DataDisk.status.in_(DISK_ARREARS_CHAIN_STATUSES),
                )
            )
        ).scalars()
    )
    for disk in disks:
        if not in_arrears:
            changed += _recover_disk(disk, now)
            continue
        changed += await _advance_disk_arrears(
            session,
            disk,
            now,
            grace_days=policies.disk_grace_days,
            frozen_days=policies.disk_frozen_days,
            settle_pending=settle_pending,
        )
    return changed
