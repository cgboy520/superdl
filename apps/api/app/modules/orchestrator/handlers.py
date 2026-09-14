"""outbox 任务处理器:实际的 K8s 副作用在这里发生。全部幂等(at-least-once)。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.errors import AppError, ErrorCode
from app.core.k8s import (
    NodePortTaken,
    ensure_registry_pull_secret,
    get_orchestrator,
)
from app.core.k8s.base import data_disk_pvc_name
from app.core.logging import get_logger
from app.core.money import hourly_cost
from app.core.outbox import OutboxTask, RetryPolicy, outbox_handler
from app.core.pricing import MARKET_SUBSCRIPTION
from app.modules.billing import service as billing_service
from app.modules.notify import service as notify_service
from app.modules.orchestrator import statemachine as sm_def
from app.modules.orchestrator.models import DataDisk, Instance
from app.modules.orchestrator.ports import block_port, ensure_port
from app.modules.orchestrator.queries import lock_instance
from app.modules.orchestrator.service import build_pod_spec_with_cluster
from app.modules.orchestrator.transitions import transition

logger = get_logger(__name__)


async def _load(session: AsyncSession, task: OutboxTask) -> Instance | None:
    instance = await session.get(Instance, task.payload["instance_id"])
    if instance is None:
        logger.warning("outbox_instance_missing", task_id=task.id, payload=task.payload)
    return instance


async def _create_with_port_recovery(session: AsyncSession, instance: Instance) -> None:
    """建 Pod/Service/路由;NodePort 被集群其它对象占用时把端口标 blocked 后重试。"""
    orch = get_orchestrator()
    # 不开 SSH 的实例不进端口池
    if instance.with_ssh:
        instance.ssh_port = await ensure_port(session, instance)
    await orch.ensure_namespace(instance.k8s_namespace)
    # Harbor 拉取凭据托管到租户 ns(指纹相同不覆写);未配机器人则 Pod 不引用
    pull_secret = await ensure_registry_pull_secret(session, instance.k8s_namespace)
    try:
        await orch.create_instance(
            await build_pod_spec_with_cluster(session, instance, image_pull_secret=pull_secret)
        )
    except NodePortTaken as exc:
        # 先取 id 再回滚(rollback 后对象过期)
        instance_id = instance.id
        # 先回滚再标记(block_port 走独立事务)
        await session.rollback()
        await block_port(
            get_sessionmaker(),
            exc.port,
            reason="node port taken by cluster object",
            expected_instance_id=instance_id,
        )
        raise


async def _provision(session: AsyncSession, task: OutboxTask, expected: str) -> None:
    """create/start 同体:建 Pod/Service/路由;状态推进交给 reconciler。"""
    instance = await _load(session, task)
    if instance is None or instance.status != expected:
        return  # 已失败/已推进,幂等跳过
    await _create_with_port_recovery(session, instance)
    # FOR UPDATE 重读:已被 reconciler 推进则回滚本事务,Pod 由泄漏回收收敛
    fresh = await session.get(Instance, instance.id, with_for_update=True)
    if fresh is None or fresh.status != expected:
        await session.rollback()


@outbox_handler("instance.create")
async def handle_create(session: AsyncSession, task: OutboxTask) -> None:
    await _provision(session, task, sm_def.CREATING)


@outbox_handler("instance.start")
async def handle_start(session: AsyncSession, task: OutboxTask) -> None:
    await _provision(session, task, sm_def.STARTING)


async def _delete_pod(session: AsyncSession, task: OutboxTask, expected: str) -> None:
    """stop/release 同体:删 Pod/Service/Ingress;后续边由 reconciler 观察到 Pod 消失后完成。"""
    instance = await _load(session, task)
    if instance is None or instance.status != expected:
        return
    await get_orchestrator().delete_instance(instance.k8s_namespace, instance.uuid)


@outbox_handler("instance.stop", retry=RetryPolicy(timeout_seconds=180))
async def handle_stop(session: AsyncSession, task: OutboxTask) -> None:
    await _delete_pod(session, task, sm_def.STOPPING)


# 重试预算 8 次 ≈ 40 分钟,覆盖 Pod 优雅删除期
@outbox_handler("instance.restart", retry=RetryPolicy(max_retries=8))
async def handle_restart(session: AsyncSession, task: OutboxTask) -> None:
    """重启:stopping → 删 Pod → 等对象消失 → stopped(尾账)→ 余额校验 → starting → 建 Pod;
    崩溃重试按当前状态续跑。"""
    instance = await _load(session, task)
    if instance is None:
        return
    orch = get_orchestrator()
    if instance.status == sm_def.STOPPING:
        await orch.delete_instance(instance.k8s_namespace, instance.uuid)
        st = await orch.get_status(instance.k8s_namespace, instance.uuid)
        if st.exists:
            # 对象仍在:抛错回滚,outbox 退避重试
            raise RuntimeError(f"pod {instance.uuid} still terminating; restart resumes on retry")
        await transition(session, instance, sm_def.STOPPED, reason="restart", actor="system")
        # 尾账与 stopped 边先单独提交
        await session.commit()
    if instance.status == sm_def.STOPPED:
        # 锁序 instance → wallet;锁内重读
        fresh_stopped = await lock_instance(session, instance.id)
        if fresh_stopped is None or fresh_stopped.status != sm_def.STOPPED:
            return  # 已被并发路径推进/删除,幂等退出
        instance = fresh_stopped
        try:
            if instance.market == MARKET_SUBSCRIPTION:
                # 与 service.start_instance 同口径:包周期不看余额,只看周期未过
                await billing_service.assert_subscription_active(session, instance.id)
            else:
                estimate = hourly_cost(instance.price_hourly, instance.gpu_count)
                await billing_service.assert_can_afford(
                    session, instance.user_id, additional_hourly=estimate
                )
        except AppError as exc:
            if exc.code is ErrorCode.INSUFFICIENT_BALANCE:
                # 余额不足不重试:停在 stopped 并通知
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
                    target_id=instance.uuid,
                )
                return
            if exc.code is ErrorCode.SUBSCRIPTION_EXPIRED:
                # 包周期到期不重试:停在 stopped 并通知
                logger.warning("restart_aborted_subscription_expired", instance_id=instance.id)
                await notify_service.notify(
                    session,
                    instance.user_id,
                    type_="instance",
                    title="重启未完成:包周期已到期",
                    content=(f"实例「{instance.name}」已关机;包周期已到期,续费后可自行开机。"),
                    severity="warning",
                    dedup_key=f"restart_subscription_expired:{instance.id}",
                    target_id=instance.uuid,
                )
                return
            raise
        await transition(session, instance, sm_def.STARTING, reason="restart", actor="system")
        instance.unready_since = None  # 同 start_instance
        # STARTING 先落库再建 Pod
        await session.commit()
        await _create_with_port_recovery(session, instance)
    elif instance.status == sm_def.STARTING:
        # 重试承接:已是 starting,直接续建
        await _create_with_port_recovery(session, instance)


@outbox_handler("instance.release", retry=RetryPolicy(timeout_seconds=300))
async def handle_release(session: AsyncSession, task: OutboxTask) -> None:
    await _delete_pod(session, task, sm_def.RELEASING)


# 等 Pod 消失再删实例盘:预算 12×30s ≈ 1.5h
@outbox_handler("instance.disk_cleanup", retry=RetryPolicy(max_retries=12, backoff_base_seconds=30))
async def handle_instance_disk_cleanup(session: AsyncSession, task: OutboxTask) -> None:
    """实例盘延迟回收(first_boot 失败的 FAILED / 释放收尾的 RELEASED):Pod 还在就抛错重试;
    死信由 reconciler 重派。"""
    instance = await _load(session, task)
    if instance is None or instance.status not in (sm_def.FAILED, sm_def.RELEASED):
        return  # 已被恢复等路径推进,无需再清
    orch = get_orchestrator()
    st = await orch.get_status(instance.k8s_namespace, instance.uuid)
    if st.exists:
        raise RuntimeError(f"pod {instance.uuid} still exists; disk cleanup resumes on retry")
    await orch.delete_instance_disk(instance.k8s_namespace, instance.uuid)


@outbox_handler("disk.provision", retry=RetryPolicy(max_retries=8, backoff_base_seconds=30))
async def handle_disk_provision(session: AsyncSession, task: OutboxTask) -> None:
    """建或扩数据盘 PVC(容量即硬配额),成功置 provisioned;死信由 reconciler 重派。"""
    disk = await session.get(DataDisk, task.payload["disk_id"])
    if disk is None or disk.status in ("deleting", "deleted"):
        return  # 删除链路已接手,不再下发
    namespace = f"{get_settings().k8s_namespace_prefix}{disk.user_id}"
    await get_orchestrator().ensure_data_disk(
        namespace, data_disk_pvc_name(disk.uuid), disk.size_gb
    )
    disk.provisioned = True
    logger.info("disk_provisioned", disk_id=disk.id, capacity_gb=disk.size_gb)


# 删 PVC 由 CSI 异步销毁 subvolume,预算宽松
@outbox_handler(
    "disk.deprovision",
    retry=RetryPolicy(max_retries=12, backoff_base_seconds=30, timeout_seconds=120),
)
async def handle_disk_deprovision(session: AsyncSession, task: OutboxTask) -> None:
    """删数据盘 PVC(reclaimPolicy=Delete,CSI 随之销毁 subvolume)后置 deleted。"""
    disk = await session.get(DataDisk, task.payload["disk_id"])
    if disk is None or disk.status != "deleting":
        return
    namespace = f"{get_settings().k8s_namespace_prefix}{disk.user_id}"
    await get_orchestrator().delete_data_disk(namespace, data_disk_pvc_name(disk.uuid))
    logger.info("disk_deprovisioned", disk_id=disk.id)
    disk.status = "deleted"
    disk.mounted_instance_id = None
