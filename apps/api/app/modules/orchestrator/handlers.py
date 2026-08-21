"""outbox 任务处理器:实际的 K8s 副作用在这里发生。全部幂等(at-least-once)。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.core.errors import AppError, ErrorCode
from app.core.k8s import NodePortTaken, get_orchestrator
from app.core.logging import get_logger
from app.core.money import as_amount
from app.core.outbox import OutboxTask, RetryPolicy, outbox_handler
from app.modules.billing import service as billing_service
from app.modules.notify import service as notify_service
from app.modules.orchestrator import statemachine as sm_def
from app.modules.orchestrator.models import Instance
from app.modules.orchestrator.service import (
    block_port,
    build_pod_spec_with_cluster,
    ensure_port,
    transition,
)

logger = get_logger(__name__)


async def _load(session: AsyncSession, task: OutboxTask) -> Instance | None:
    instance = await session.get(Instance, task.payload["instance_id"])
    if instance is None:
        logger.warning("outbox_instance_missing", task_id=task.id, payload=task.payload)
    return instance


async def _create_with_port_recovery(session: AsyncSession, instance: Instance) -> None:
    """建 Pod/Service/Ingress;NodePort 被集群其它对象占用时把端口标 blocked 后重试。

    没有这一步则高水位线永远停在被占端口前面(那行 PortAllocation 随事务一起回滚),
    此后所有触顶的新建实例全部失败。
    """
    orch = get_orchestrator()
    instance.ssh_port = await ensure_port(session, instance)
    await orch.ensure_namespace(instance.k8s_namespace)
    try:
        await orch.create_instance(await build_pod_spec_with_cluster(session, instance))
    except NodePortTaken as exc:
        # 先回滚再标记:本事务里那行 PortAllocation 未提交却占着同一主键,
        # block_port 的独立事务会在它上面死等到 handler 超时。
        await session.rollback()
        await block_port(get_sessionmaker(), exc.port, reason="node port taken by cluster object")
        raise
    instance.pod_name = instance.uuid


@outbox_handler("instance.create")
async def handle_create(session: AsyncSession, task: OutboxTask) -> None:
    instance = await _load(session, task)
    if instance is None or instance.status != sm_def.CREATING:
        return  # 已失败/已推进,幂等跳过
    await _create_with_port_recovery(session, instance)
    # 状态推进交给 reconciler(Pod Ready → running / 超时 → failed)


@outbox_handler("instance.start")
async def handle_start(session: AsyncSession, task: OutboxTask) -> None:
    instance = await _load(session, task)
    if instance is None or instance.status != sm_def.STARTING:
        return
    await _create_with_port_recovery(session, instance)


@outbox_handler("instance.stop")
async def handle_stop(session: AsyncSession, task: OutboxTask) -> None:
    instance = await _load(session, task)
    if instance is None or instance.status != sm_def.STOPPING:
        return
    orch = get_orchestrator()
    await orch.delete_instance(instance.k8s_namespace, instance.uuid)
    # reconciler 观察到 Pod 消失 → stopped(尾账在计费边监听器触发)


# 重启要跨过 Pod 的优雅删除期(terminationGracePeriodSeconds=30),期间任务靠抛错退避重试;
# 放宽到 8 次 ≈ 40 分钟,免得正常的终止等待把实例卡在 stopping 进死信。
@outbox_handler("instance.restart", retry=RetryPolicy(max_retries=8))
async def handle_restart(session: AsyncSession, task: OutboxTask) -> None:
    """重启:stopping → 删 Pod → 等对象真正消失 → stopped(尾账) → 余额校验 → starting → 建 Pod。

    单事务内推进多个边,每个边都留事件;崩溃重试按当前状态续跑。
    """
    instance = await _load(session, task)
    if instance is None:
        return
    orch = get_orchestrator()
    if instance.status == sm_def.STOPPING:
        await orch.delete_instance(instance.k8s_namespace, instance.uuid)
        st = await orch.get_status(instance.k8s_namespace, instance.uuid)
        if st.exists:
            # K8s 的删除是优雅删除:对象要在 etcd 里再留 terminationGracePeriodSeconds。
            # 此时同名重建必然撞 409,而 409 当幂等跳过 = Pod 没建出来却报成功,实例会被
            # 判 schedule_timeout → failed(只能释放不能开机)。
            # 抛错回滚:实例留在 stopping,由 outbox 退避重试续跑。
            raise RuntimeError(f"pod {instance.uuid} still terminating; restart resumes on retry")
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
        try:
            await billing_service.require_balance_at_least(
                session, instance.user_id, estimate, hint_key="billing.insufficientForRestart"
            )
        except AppError as exc:
            if exc.code is not ErrorCode.INSUFFICIENT_BALANCE:
                raise
            # 余额不足不是基础设施故障:重试 5 次只会制造死信噪音。
            # 实例停在 stopped(用户可见),充值后自行开机;同时发通知说明原因。
            logger.warning("restart_aborted_insufficient_balance", instance_id=instance.id)
            await notify_service.notify(
                session,
                instance.user_id,
                type_="instance",
                title="重启未完成:余额不足",
                content=(
                    f"实例「{instance.name}」已关机;余额不足以支付 1 小时预估费用,"
                    "充值后可自行开机。"
                ),
                severity="warning",
                dedup_key=f"restart_no_balance:{instance.id}",
            )
            return
        await transition(
            session,
            instance,
            sm_def.STARTING,
            reason="restart",
            actor="system",
            metadata={"restart": True},
        )
        await _create_with_port_recovery(session, instance)


@outbox_handler("instance.release")
async def handle_release(session: AsyncSession, task: OutboxTask) -> None:
    instance = await _load(session, task)
    if instance is None:
        return
    if instance.status != sm_def.RELEASING:
        return
    orch = get_orchestrator()
    await orch.delete_instance(instance.k8s_namespace, instance.uuid)
    # releasing → released 由 reconciler 在确认 Pod 消失后完成(含擦盘事件与端口回收)


# 擦盘是轮询集群 Job 完成,不是一次性调用:默认 5 次预算会让大盘没擦完就进死信、
# 盘永久卡 deleting。放宽到约 1.5 小时。
@outbox_handler("disk.wipe", retry=RetryPolicy(max_retries=12, backoff_base_seconds=30))
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
