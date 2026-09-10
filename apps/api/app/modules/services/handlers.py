"""在线服务的 outbox 任务:版本更新收尾。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.outbox import OutboxTask, RetryPolicy, outbox_handler
from app.modules.orchestrator import service as orchestrator_service
from app.modules.services.models import Service

logger = get_logger(__name__)

RETIRE_TASK_TYPE = "service.retire"

# 旧版本要先走完关机(Pod 优雅删除 30s + 巡检确认),期间靠抛错退避;预算 ≈ 30 × 20s 指数退避
_RETIRE_RETRY = RetryPolicy(max_retries=30, backoff_base_seconds=20)


@outbox_handler(RETIRE_TASK_TYPE, retry=_RETIRE_RETRY)
async def handle_retire(session: AsyncSession, task: OutboxTask) -> None:
    """新版本 running 后释放旧版本实例。幂等:旧实例仍是当前版本(翻转被回滚)/ 已在释放 → 直接返回;
    还没停完 → 抛错退避等下一轮(不在这里强删 Pod)。锁序 instance → service(只读 service)。"""
    old = await orchestrator_service.lock_instance(session, task.payload["instance_id"])
    if old is None:
        return
    svc = await session.get(Service, task.payload["service_id"])
    if svc is None or svc.current_instance_id == old.id:
        return
    if old.status in (orchestrator_service.RELEASING, orchestrator_service.RELEASED):
        return
    if old.status not in (
        orchestrator_service.STOPPING,
        orchestrator_service.STOPPED,
        orchestrator_service.FROZEN,
        orchestrator_service.FAILED,
    ):
        raise RuntimeError(f"rollout_retire_wait: instance {old.id} is {old.status}")
    await orchestrator_service.release_instance_row(
        session, old, actor="system", reason="rollout_retire"
    )
    logger.info("service_rollout_retired", service_id=svc.id, instance_id=old.id)
