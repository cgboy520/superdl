"""实例状态迁移原语(由 service.py 门面再导出):

校验 + 乐观锁更新 + 同事务落 instance_events + 迁移监听器(billing 尾账注册在此)。
"""

from collections.abc import Awaitable, Callable
from typing import Any, cast

from sqlalchemy import CursorResult, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import conflict
from app.core.timeutil import now_utc
from app.modules.orchestrator.models import Instance, InstanceEvent
from app.modules.orchestrator.statemachine import validate_transition

# 迁移监听器:billing 注册尾账/计费边处理,与状态迁移同事务
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
    """校验 + 乐观锁更新 + 落事件 + 触发监听器。不 commit,由调用方控制事务。"""
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
        created_at=now_utc(),  # 计费依赖精确时刻,显式生成而非 server_default
    )
    session.add(event)
    await session.flush()
    for listener in _transition_listeners:
        await listener(session, instance, event)
    return event
