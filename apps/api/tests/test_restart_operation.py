"""Restart retries must belong to the current unfinished command, not a later lifecycle."""

# pyright: reportPrivateUsage=false

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.outbox import OutboxTask
from app.modules.orchestrator import handlers
from app.modules.orchestrator.models import Instance, InstanceEvent


def _events(*, stopped_reason: str = "restart") -> list[InstanceEvent]:
    return [
        InstanceEvent(
            id=10, from_status="running", to_status="stopping", reason="restart", actor="user"
        ),
        InstanceEvent(
            id=11,
            from_status="stopping",
            to_status="stopped",
            reason=stopped_reason,
            actor="system",
        ),
        InstanceEvent(
            id=12, from_status="stopped", to_status="starting", reason="restart", actor="system"
        ),
    ]


@pytest.mark.parametrize("reason", ["restart", "pod_deleted"])
@pytest.mark.parametrize("count,status", [(1, "stopping"), (2, "stopped"), (3, "starting")])
def test_only_unfinished_command_prefix_can_resume(reason, count, status):
    assert handlers._is_restart_prefix(_events(stopped_reason=reason)[:count], 10, status)


def test_later_user_stop_is_not_an_intermediate_restart_stage():
    events = [
        *_events(),
        InstanceEvent(
            id=13, from_status="starting", to_status="running", reason="pod_ready", actor="system"
        ),
        InstanceEvent(
            id=14, from_status="running", to_status="stopping", reason="user_stop", actor="user"
        ),
        InstanceEvent(
            id=15, from_status="stopping", to_status="stopped", reason="pod_deleted", actor="system"
        ),
    ]
    assert not handlers._is_restart_prefix(events[:4], 10, "stopped")
    assert not handlers._is_restart_prefix(events, 10, "stopped")


@pytest.mark.parametrize("change", ["other_origin", "manual_start", "different_reason", "released"])
def test_other_operation_does_not_match_restart(change):
    events = _events()
    origin, status = 10, "starting"
    if change == "other_origin":
        origin = 9
    elif change == "manual_start":
        events[-1].actor = "user"
        events[-1].reason = "user_start"
    elif change == "different_reason":
        events[0].reason = "user_stop"
    else:
        status = "released"
    assert not handlers._is_restart_prefix(events, origin, status)


@pytest.mark.parametrize("origin", [None, 0, -1, True, "10"])
async def test_legacy_or_invalid_command_is_noop(monkeypatch, origin):
    locked = AsyncMock()
    create = AsyncMock()
    monkeypatch.setattr(handlers, "lock_instance", locked)
    monkeypatch.setattr(handlers, "_create_with_port_recovery", create)
    task = OutboxTask(id=7, payload={"instance_id": 1, "restart_event_id": origin})
    await handlers.handle_restart(MagicMock(spec=AsyncSession), task)
    locked.assert_not_awaited()
    create.assert_not_awaited()


async def test_handler_rejects_later_stop_before_any_kubernetes_call(monkeypatch):
    session = MagicMock(spec=AsyncSession)
    result = MagicMock()
    result.scalars.return_value = [
        *_events(),
        InstanceEvent(
            id=13, from_status="starting", to_status="running", reason="pod_ready", actor="system"
        ),
    ]
    session.execute.return_value = result
    instance = Instance(id=1, status="stopped")
    locked = AsyncMock(return_value=instance)
    create = AsyncMock()
    orchestrator = MagicMock(side_effect=AssertionError("stale task must not reach Kubernetes"))
    monkeypatch.setattr(handlers, "lock_instance", locked)
    monkeypatch.setattr(handlers, "get_orchestrator", orchestrator)
    monkeypatch.setattr(handlers, "_create_with_port_recovery", create)
    task = OutboxTask(id=7, payload={"instance_id": 1, "restart_event_id": 10})
    await handlers.handle_restart(session, task)
    locked.assert_awaited_once_with(session, 1)
    create.assert_not_awaited()
    session.commit.assert_not_awaited()


async def test_starting_retry_holds_instance_lock_through_provision(monkeypatch):
    session = MagicMock(spec=AsyncSession)
    result = MagicMock()
    result.scalars.return_value = _events()
    session.execute.return_value = result
    instance = Instance(id=1, status="starting")
    locked = AsyncMock(return_value=instance)
    monkeypatch.setattr(handlers, "lock_instance", locked)
    monkeypatch.setattr(handlers, "get_orchestrator", MagicMock())

    async def provision(current_session, current_instance):
        locked.assert_awaited_once_with(session, 1)
        assert current_instance is instance
        current_session.commit.assert_not_awaited()

    create = AsyncMock(side_effect=provision)
    monkeypatch.setattr(handlers, "_create_with_port_recovery", create)
    task = OutboxTask(id=7, payload={"instance_id": 1, "restart_event_id": 10})
    await handlers.handle_restart(session, task)
    create.assert_awaited_once_with(session, instance)


async def test_restart_rechecks_command_after_internal_commit(monkeypatch):
    session = MagicMock(spec=AsyncSession)
    instance = Instance(id=1, status="stopped")
    load = AsyncMock(side_effect=[instance, instance, None])
    monkeypatch.setattr(handlers, "_load_restart", load)
    monkeypatch.setattr(handlers, "get_orchestrator", MagicMock())
    monkeypatch.setattr(handlers, "_restart_affordable", AsyncMock(return_value=True))

    async def transition(_session, row, status, **_kwargs):
        row.status = status

    monkeypatch.setattr(handlers, "transition", transition)
    create = AsyncMock()
    monkeypatch.setattr(handlers, "_create_with_port_recovery", create)
    task = OutboxTask(id=7, payload={"instance_id": 1, "restart_event_id": 10})
    await handlers.handle_restart(session, task)
    session.commit.assert_awaited_once()
    assert load.await_count == 3
    create.assert_not_awaited()
