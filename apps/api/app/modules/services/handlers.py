"""在线服务的 outbox 任务:版本更新收尾。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.outbox import OutboxTask, RetryPolicy, outbox_handler
from app.modules.orchestrator import (
    queries as orchestrator_queries,
    service as orchestrator_service,
    statemachine as sm_def,
)
from app.modules.services.models import Service

logger = get_logger(__name__)

RETIRE_TASK_TYPE = "service.retire"

_RETIRE_RETRY = RetryPolicy(max_retries=30, backoff_base_seconds=20)


@outbox_handler(RETIRE_TASK_TYPE, retry=_RETIRE_RETRY)
async def handle_retire(session: AsyncSession, task: OutboxTask) -> None:
    """先锁旧实例再读服务;非当前且处于 stopping/stopped/frozen/failed 时调用释放原语。

    实例或服务缺失、仍为当前版本、已在释放时返回;其他状态抛错重试。
    """
    old = await orchestrator_queries.lock_instance(session, task.payload["instance_id"])
    if old is None:
        return
    svc = await session.get(Service, task.payload["service_id"])
    if svc is None or svc.current_instance_id == old.id:
        return
    if old.status in (sm_def.RELEASING, sm_def.RELEASED):
        return
    if old.status not in (
        sm_def.STOPPING,
        sm_def.STOPPED,
        sm_def.FROZEN,
        sm_def.FAILED,
    ):
        raise RuntimeError(f"rollout_retire_wait: instance {old.id} is {old.status}")
    await orchestrator_service.release_instance_row(
        session, old, actor="system", reason="rollout_retire"
    )
    logger.info("service_rollout_retired", service_id=svc.id, instance_id=old.id)
