"""outbox 任务处理器:实际的 K8s 副作用在这里发生。全部幂等(at-least-once)。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.k8s import get_orchestrator
from app.core.logging import get_logger
from app.core.money import as_amount
from app.core.outbox import OutboxTask, outbox_handler
from app.modules.billing import service as billing_service
from app.modules.orchestrator import statemachine as sm_def
from app.modules.orchestrator.models import Instance
from app.modules.orchestrator.service import build_pod_spec, ensure_port, transition

logger = get_logger(__name__)


async def _load(session: AsyncSession, task: OutboxTask) -> Instance | None:
    instance = await session.get(Instance, task.payload["instance_id"])
    if instance is None:
        logger.warning("outbox_instance_missing", task_id=task.id, payload=task.payload)
    return instance


@outbox_handler("instance.create")
async def handle_create(session: AsyncSession, task: OutboxTask) -> None:
    instance = await _load(session, task)
    if instance is None or instance.status != sm_def.CREATING:
        return  # 已失败/已推进,幂等跳过
    orch = get_orchestrator()
    instance.ssh_port = await ensure_port(session, instance)
    assert instance.k8s_namespace is not None
    await orch.ensure_namespace(instance.k8s_namespace)
    await orch.create_instance(build_pod_spec(instance))
    instance.pod_name = instance.uuid
    # 状态推进交给 reconciler(Pod Ready → running / 超时 → failed)


@outbox_handler("instance.start")
async def handle_start(session: AsyncSession, task: OutboxTask) -> None:
    instance = await _load(session, task)
    if instance is None or instance.status != sm_def.STARTING:
        return
    orch = get_orchestrator()
    instance.ssh_port = await ensure_port(session, instance)
    assert instance.k8s_namespace is not None
    await orch.ensure_namespace(instance.k8s_namespace)
    await orch.create_instance(build_pod_spec(instance))
    instance.pod_name = instance.uuid


@outbox_handler("instance.stop")
async def handle_stop(session: AsyncSession, task: OutboxTask) -> None:
    instance = await _load(session, task)
    if instance is None or instance.status != sm_def.STOPPING:
        return
    orch = get_orchestrator()
    assert instance.k8s_namespace is not None
    await orch.delete_instance(instance.k8s_namespace, instance.uuid)
    # reconciler 观察到 Pod 消失 → stopped(尾账在计费边监听器触发)


@outbox_handler("instance.restart")
async def handle_restart(session: AsyncSession, task: OutboxTask) -> None:
    """重启:stopping → 删 Pod → stopped(尾账) → 余额校验 → starting → 建 Pod。

    单事务内推进多个边,每个边都留事件;崩溃重试按当前状态续跑。
    """
    instance = await _load(session, task)
    if instance is None:
        return
    orch = get_orchestrator()
    assert instance.k8s_namespace is not None
    if instance.status == sm_def.STOPPING:
        await orch.delete_instance(instance.k8s_namespace, instance.uuid)
        await transition(
            session,
            instance,
            sm_def.STOPPED,
            reason="restart",
            actor="system",
            metadata={"restart": True},
        )
    if instance.status == sm_def.STOPPED:
        estimate = as_amount(instance.price_hourly * instance.gpu_count)
        await billing_service.require_balance_at_least(
            session, instance.user_id, estimate, hint_key="billing.insufficientForRestart"
        )
        await transition(
            session,
            instance,
            sm_def.STARTING,
            reason="restart",
            actor="system",
            metadata={"restart": True},
        )
        instance.ssh_port = await ensure_port(session, instance)
        await orch.create_instance(build_pod_spec(instance))


@outbox_handler("instance.release")
async def handle_release(session: AsyncSession, task: OutboxTask) -> None:
    instance = await _load(session, task)
    if instance is None:
        return
    if instance.status != sm_def.RELEASING:
        return
    orch = get_orchestrator()
    assert instance.k8s_namespace is not None
    await orch.delete_instance(instance.k8s_namespace, instance.uuid)
    # releasing → released 由 reconciler 在确认 Pod 消失后完成(含擦盘事件与端口回收)


@outbox_handler("disk.wipe")
async def handle_disk_wipe(session: AsyncSession, task: OutboxTask) -> None:
    """真实擦除 JuiceFS 子路径(集群侧 Job)后置 deleted。
    wipe_disk 幂等:Job 未完成抛错 → outbox 退避重试,完成后本 handler 收尾状态。"""
    from app.core.config import get_settings
    from app.modules.orchestrator.models import DataDisk

    disk = await session.get(DataDisk, task.payload["disk_id"])
    if disk is None or disk.status != "deleting":
        return
    namespace = f"{get_settings().k8s_namespace_prefix}{disk.user_id}"
    await get_orchestrator().wipe_disk(namespace, disk.juicefs_subpath)
    logger.info("disk_wiped", disk_id=disk.id, subpath=disk.juicefs_subpath)
    disk.status = "deleted"
    disk.mounted_instance_id = None
