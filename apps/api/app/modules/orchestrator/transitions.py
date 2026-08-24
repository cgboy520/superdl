"""实例状态迁移原语(从 service.py 拆出,门面再导出):

校验 + 乐观锁更新 + 同事务落 instance_events + 迁移监听器(billing 尾账注册在此)。
"""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, cast

from fastapi import status as http_status
from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
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
        raise AppError(
            ErrorCode.CONFLICT,
            key="orchestrator.stateChangedRetry",
            http_status=http_status.HTTP_409_CONFLICT,
        )
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


async def last_entered_status_at(
    session: AsyncSession, instance_id: int, to_status: str
) -> datetime | None:
    """最近一次进入某状态的事件时刻(无则 None)。
    计费边监听器据此识别 unready_since 是否为上次失联 episode 的残留。"""
    return (
        await session.execute(
            select(InstanceEvent.created_at)
            .where(
                InstanceEvent.instance_id == instance_id,
                InstanceEvent.to_status == to_status,  # 退出边(to_status≠目标)天然被排除
            )
            .order_by(InstanceEvent.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
