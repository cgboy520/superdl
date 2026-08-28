"""outbox 任务处理器:实际的 K8s 副作用在这里发生。全部幂等(at-least-once)。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.core.errors import AppError, ErrorCode
from app.core.k8s import NodePortTaken, get_orchestrator
from app.core.logging import get_logger
from app.core.money import hourly_cost
from app.core.outbox import OutboxTask, RetryPolicy, outbox_handler
from app.core.pricing import MARKET_SUBSCRIPTION
from app.core.registry import ensure_registry_pull_secret
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
    """建 Pod/Service/路由;NodePort 被集群其它对象占用时把端口标 blocked 后重试。"""
    orch = get_orchestrator()
    # 不开 SSH 的实例不进端口池:端口池只有 30000–32767 一段(与 K8s NodePort 同段),
    # 是全平台硬上限
    if instance.with_ssh:
        instance.ssh_port = await ensure_port(session, instance)
    await orch.ensure_namespace(instance.k8s_namespace)
    # Harbor 拉取凭据:按生效配置托管到租户 ns(指纹相同不覆写),未配机器人则 Pod 不引用
    pull_secret = await ensure_registry_pull_secret(session, instance.k8s_namespace)
    try:
        await orch.create_instance(
            await build_pod_spec_with_cluster(session, instance, image_pull_secret=pull_secret)
        )
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


async def _provision(session: AsyncSession, task: OutboxTask, expected: str) -> None:
    """create/start 同体:建 Pod/Service/路由;状态推进交给 reconciler
    (Pod Ready → running / 超时 → failed)。"""
    instance = await _load(session, task)
    if instance is None or instance.status != expected:
        return  # 已失败/已推进,幂等跳过
    await _create_with_port_recovery(session, instance)
    # 建 Pod 耗时可能跨过超时线:FOR UPDATE 重读,已被 reconciler 推进(超时 failed +
    # 端口已回收)则回滚本事务——failed 实例不能占端口;已建出的 Pod 由泄漏回收收敛
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
    """stop/release 同体:删 Pod/Service/Ingress;后续边(stopped 的尾账 / released 的
    端口回收与实例盘回收入队)由 reconciler 观察到 Pod 消失后完成。"""
    instance = await _load(session, task)
    if instance is None or instance.status != expected:
        return
    await get_orchestrator().delete_instance(instance.k8s_namespace, instance.uuid)


@outbox_handler("instance.stop")
async def handle_stop(session: AsyncSession, task: OutboxTask) -> None:
    await _delete_pod(session, task, sm_def.STOPPING)


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
        await transition(session, instance, sm_def.STOPPED, reason="restart", actor="system")
        # 尾账与 stopped 边先单独落库:后续建 Pod 撞 NodePortTaken 会 rollback 本事务,
        # 不分开提交会把已完成的迁移和尾账一起回滚掉(尾账丢失 = 少计停机前费用)
        await session.commit()
    if instance.status == sm_def.STOPPED:
        try:
            if instance.market == MARKET_SUBSCRIPTION:
                # 与开机同口径(service.start_instance):包周期已预付整段周期,重启不看余额;
                # 但周期已过不能再开,否则已到期实例可借重启绕过开机门禁继续运行
                await billing_service.assert_subscription_active(session, instance.id)
            else:
                estimate = hourly_cost(instance.price_hourly, instance.gpu_count)
                await billing_service.assert_can_afford(
                    session, instance.user_id, additional_hourly=estimate
                )
        except AppError as exc:
            if exc.code is ErrorCode.INSUFFICIENT_BALANCE:
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
            if exc.code is ErrorCode.SUBSCRIPTION_EXPIRED:
                # 包周期到期同样不走重试:实例停在 stopped,续费后由用户自行开机
                logger.warning("restart_aborted_subscription_expired", instance_id=instance.id)
                await notify_service.notify(
                    session,
                    instance.user_id,
                    type_="instance",
                    title="重启未完成:包周期已到期",
                    content=(f"实例「{instance.name}」已关机;包周期已到期,续费后可自行开机。"),
                    severity="warning",
                    dedup_key=f"restart_subscription_expired:{instance.id}",
                )
                return
            raise
        await transition(session, instance, sm_def.STARTING, reason="restart", actor="system")
        instance.unready_since = None  # 新一轮就绪观察从零起算(同 start_instance)
        # STARTING 先落库再建 Pod:建 Pod 期间 DB 已是 starting,泄漏回收对在途状态
        # 有宽限,不会在「DB stopped + Pod 已建」窗口把实例当泄漏强删
        await session.commit()
        await _create_with_port_recovery(session, instance)
    elif instance.status == sm_def.STARTING:
        # 承接分支:上次在建 Pod 前/中崩溃,任务重试时状态已是 starting——直接续建
        await _create_with_port_recovery(session, instance)


@outbox_handler("instance.release")
async def handle_release(session: AsyncSession, task: OutboxTask) -> None:
    await _delete_pod(session, task, sm_def.RELEASING)


# 等 Pod 消失再删实例盘:预算 12×30s ≈ 1.5h,覆盖长 Terminating
@outbox_handler("instance.disk_cleanup", retry=RetryPolicy(max_retries=12, backoff_base_seconds=30))
async def handle_instance_disk_cleanup(session: AsyncSession, task: OutboxTask) -> None:
    """实例盘延迟回收(first_boot 失败的 FAILED 实例 / 释放收尾的 RELEASED 实例入队)。

    delete_instance_disk 的前置条件是 Pod 已消失(pvc-protection 会挂起 LV 回收),Pod 还在
    就抛错退避重试;死信后由 reconciler 的死信重派兜底,不会残留孤儿 LV。"""
    instance = await _load(session, task)
    if instance is None or instance.status not in (sm_def.FAILED, sm_def.RELEASED):
        return  # 已被恢复等路径推进,无需再清
    orch = get_orchestrator()
    st = await orch.get_status(instance.k8s_namespace, instance.uuid)
    if st.exists:
        raise RuntimeError(f"pod {instance.uuid} still exists; disk cleanup resumes on retry")
    await orch.delete_instance_disk(instance.k8s_namespace, instance.uuid)


@outbox_handler("disk.quota", retry=RetryPolicy(max_retries=8, backoff_base_seconds=30))
async def handle_disk_quota(session: AsyncSession, task: OutboxTask) -> None:
    """下发 JuiceFS 目录硬配额(CLI Job,纯元数据)。成功置 quota_synced;
    预算耗尽转死信后由 reconciler 周期重派(配额永不留缺口)。"""
    from app.modules.orchestrator.models import DataDisk

    disk = await session.get(DataDisk, task.payload["disk_id"])
    if disk is None or disk.status in ("deleting", "deleted"):
        return  # 删除链路有自己的配额摘除步,不下发
    await get_orchestrator().set_disk_quota(disk.juicefs_subpath, disk.size_gb)
    disk.quota_synced = True
    logger.info("disk_quota_synced", disk_id=disk.id, capacity_gb=disk.size_gb)


# 擦盘是轮询集群 Job 完成而非一次性调用,预算放宽到约 1.5 小时
@outbox_handler("disk.wipe", retry=RetryPolicy(max_retries=12, backoff_base_seconds=30))
async def handle_disk_wipe(session: AsyncSession, task: OutboxTask) -> None:
    """真实擦除 JuiceFS 子路径(集群侧 Job)后置 deleted。
    wipe_disk 幂等:Job 未完成抛错 → outbox 退避重试,完成后本 handler 收尾状态。
    擦除前先摘除目录配额(失败仅告警:残留配额条目指向将被擦除的目录,无实际影响)。"""
    from app.core.config import get_settings
    from app.modules.orchestrator.models import DataDisk

    disk = await session.get(DataDisk, task.payload["disk_id"])
    if disk is None or disk.status != "deleting":
        return
    try:
        await get_orchestrator().delete_disk_quota(disk.juicefs_subpath)
    except Exception:
        logger.warning("disk_quota_delete_failed", disk_id=disk.id, exc_info=True)
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
