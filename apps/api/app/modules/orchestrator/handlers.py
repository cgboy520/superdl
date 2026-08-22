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
    """建 Pod/Service/Ingress;NodePort 被集群其它对象占用时把端口标 blocked 后重试。"""
    orch = get_orchestrator()
    instance.ssh_port = await ensure_port(session, instance)
    await orch.ensure_namespace(instance.k8s_namespace)
    try:
        await orch.create_instance(await build_pod_spec_with_cluster(session, instance))
    except NodePortTaken as exc:
        # 先取 id 再回滚:rollback 后对象过期,访问属性会触发异步上下文外的懒加载
        instance_id = instance.id
        # 先回滚再标记:本事务未提交的同主键 PortAllocation 会锁死 block_port 的独立事务
        await session.rollback()
        await block_port(
            get_sessionmaker(),
            exc.port,
            reason="node port taken by cluster object",
            expected_instance_id=instance_id,
        )
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


# 重启要跨过 Pod 的优雅删除期(terminationGracePeriodSeconds=30),期间靠抛错退避重试;
# 预算放宽到 8 次 ≈ 40 分钟
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
            # 优雅删除期内对象仍在 etcd,同名重建必撞 409。
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
        # 尾账与 stopped 边先单独落库:后续建 Pod 撞 NodePortTaken 会 rollback 本事务,
        # 不分开提交会把已完成的迁移和尾账一起回滚掉(尾账丢失 = 少计停机前费用)
        await session.commit()
    if instance.status == sm_def.STOPPED:
        estimate = as_amount(instance.price_hourly * instance.gpu_count)
        try:
            await billing_service.assert_can_afford(
                session, instance.user_id, additional_hourly=estimate
            )
        except AppError as exc:
            if exc.code is not ErrorCode.INSUFFICIENT_BALANCE:
                raise
            # 余额不足不走重试:实例停在 stopped,发通知说明,充值后由用户自行开机
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


# 擦盘是轮询集群 Job 完成而非一次性调用,预算放宽到约 1.5 小时
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
    try:
        await get_orchestrator().wipe_disk(namespace, disk.juicefs_subpath)
    except Exception as exc:
        # 租户 ns 不存在(从未建过实例即删盘):无物可擦,视为完成而非死信。
        # ApiException 不能 import(业务代码不碰 kubernetes 客户端),按 status 属性鸭子判定。
        if getattr(exc, "status", None) != 404:
            raise
        logger.warning("disk_wipe_namespace_missing", disk_id=disk.id, namespace=namespace)
    logger.info("disk_wiped", disk_id=disk.id, subpath=disk.juicefs_subpath)
    disk.status = "deleted"
    disk.mounted_instance_id = None
