"""reconciler:每 30s 全量比对「DB 期望 ↔ K8s 实际」并收敛。

- creating/starting + Pod Ready → running(计费开始)
- creating/starting 超时未 Ready → failed(全额退=无账)+ 清理
- running + Pod 消失/异常/持续 not-ready → failed(停止计费)+ 告警 + 通知用户
- stopping + Pod 消失 → stopped(计费边,尾账监听器触发)
- releasing + Pod 消失 → released(擦盘事件 + 端口回收)
- K8s 存在但 DB 已终态的 Pod → 强制删除(清理泄漏)
"""

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.k8s import PodStatus, get_orchestrator
from app.core.locks import LockKey, try_advisory_lock
from app.core.logging import get_logger
from app.core.metrics import INSTANCE_NODE_LOST_TOTAL, RECONCILE_LEAKED_TOTAL
from app.core.timeutil import ensure_utc, now_utc
from app.modules.notify import service as notify_service
from app.modules.orchestrator import statemachine as sm_def
from app.modules.orchestrator.disks import detach_for_instance
from app.modules.orchestrator.models import Instance, InstanceEvent
from app.modules.orchestrator.service import free_port, transition

logger = get_logger(__name__)

ACTIVE_STATUSES = (
    sm_def.CREATING,
    sm_def.STARTING,
    sm_def.RUNNING,
    sm_def.STOPPING,
    sm_def.RELEASING,
)


async def reconcile_once(sm: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """单轮对账。advisory lock 保证多副本单实例执行。返回动作计数(测试/指标用)。"""
    counts = {"to_running": 0, "to_failed": 0, "to_stopped": 0, "to_released": 0, "leaked": 0}
    async with sm() as lock_session, try_advisory_lock(lock_session, LockKey.RECONCILER) as got:
        if not got:
            return counts
        await _reconcile_instances(sm, counts)
        await _reclaim_leaked_pods(sm, counts)
    return counts


async def _entered_status_at(session: AsyncSession, instance: Instance):
    """实例进入当前状态的时刻(取该状态最后一条事件)。

    不能用 updated_at 判超时:它带 onupdate,handler 回填 ssh_port/pod_name 等任何字段
    都会把计时重置,creating 超时可能永远不触发。
    """
    entered = (
        await session.execute(
            select(func.max(InstanceEvent.created_at)).where(
                InstanceEvent.instance_id == instance.id,
                InstanceEvent.to_status == instance.status,
            )
        )
    ).scalar_one_or_none()
    return ensure_utc(entered) if entered is not None else ensure_utc(instance.created_at)


async def _running_pod_lost_reason(
    session: AsyncSession, instance: Instance, st: PodStatus, unready_timeout: timedelta
) -> str | None:
    """running 实例是否已经不可用了。返回迁移 reason,None = 还活着。

    只看 exists 和 phase 不够:节点失联时 kubelet 不可达,Ready condition 被置 False,
    但 phase 仍是 Running、对象仍在 etcd 里。漏掉这一条则控制台显示「运行中」而账单照扣。
    """
    if not st.exists or st.phase in ("Failed", "Succeeded"):
        return "pod_lost"
    if st.deleting:
        return "pod_lost"  # 被驱逐/被外部删除,不是我们发起的
    if st.ready:
        return None
    # not-ready 给一段宽限:容器重启、镜像层重挂这类抖动不该误杀实例
    if instance.unready_since is None:
        instance.unready_since = now_utc()
        await session.flush()
        return None
    if now_utc() - ensure_utc(instance.unready_since) > unready_timeout:
        return "node_lost"
    return None


async def _reconcile_instances(
    sm: async_sessionmaker[AsyncSession], counts: dict[str, int]
) -> None:
    settings = get_settings()
    orch = get_orchestrator()
    timeout = timedelta(seconds=settings.creating_timeout_seconds)
    unready_timeout = timedelta(seconds=settings.running_unready_timeout_seconds)

    async with sm() as session:
        ids = (
            (await session.execute(select(Instance.id).where(Instance.status.in_(ACTIVE_STATUSES))))
            .scalars()
            .all()
        )

    for instance_id in ids:
        # 每实例独立事务:单个失败不拖垮整轮
        try:
            async with sm() as session:
                instance = await session.get(Instance, instance_id)
                if instance is None or instance.status not in ACTIVE_STATUSES:
                    continue
                st = await orch.get_status(instance.k8s_namespace, instance.uuid)

                if instance.status in (sm_def.CREATING, sm_def.STARTING):
                    if st.exists and st.ready:
                        instance.node_name = st.node_name
                        await transition(
                            session,
                            instance,
                            sm_def.RUNNING,
                            reason="pod_ready",
                            actor="system",
                        )
                        counts["to_running"] += 1
                    elif now_utc() - await _entered_status_at(session, instance) > timeout:
                        first_boot = instance.status == sm_def.CREATING
                        await transition(
                            session,
                            instance,
                            sm_def.FAILED,
                            reason="schedule_timeout",
                            actor="system",
                            metadata={"hint": "调度或拉取镜像超时,未产生任何费用"},
                        )
                        await free_port(session, instance.id)
                        await detach_for_instance(session, instance.id)
                        await orch.delete_instance(instance.k8s_namespace, instance.uuid)
                        if first_boot:
                            # creating 超时 = 这只盘从未承载过数据,回收掉不留孤儿 LV;
                            # starting 超时禁止删盘:那是停过机的实例,盘里有上一轮数据。
                            await orch.delete_instance_disk(instance.k8s_namespace, instance.uuid)
                        counts["to_failed"] += 1
                        logger.warning("instance_schedule_timeout", instance_id=instance.id)

                elif instance.status == sm_def.RUNNING:
                    lost = await _running_pod_lost_reason(session, instance, st, unready_timeout)
                    if lost is None:
                        if instance.unready_since is not None:
                            instance.unready_since = None  # 抖动恢复,重新计时
                    else:
                        await transition(
                            session,
                            instance,
                            sm_def.FAILED,
                            reason=lost,
                            actor="system",
                            metadata={
                                "phase": st.phase if st.exists else "Missing",
                                "ready": st.ready,
                            },
                        )
                        await free_port(session, instance.id)
                        await detach_for_instance(session, instance.id)
                        if st.exists:
                            # 失联节点上的 Pod 只有强删才会从 etcd 消失(kubelet 确认不了),
                            # 优雅删除会让实例永久卡在 stopping/releasing。
                            await orch.delete_instance(
                                instance.k8s_namespace, instance.uuid, force=lost == "node_lost"
                            )
                        counts["to_failed"] += 1
                        if lost == "node_lost":
                            INSTANCE_NODE_LOST_TOTAL.inc()
                            logger.error(
                                "instance_node_lost",
                                instance_id=instance.id,
                                node=instance.node_name,
                            )
                            await notify_service.notify(
                                session,
                                instance.user_id,
                                type_="instance",
                                title="实例已停止:所在节点失联",
                                content=(
                                    f"实例「{instance.name}」所在节点与集群失去联系,"
                                    "已停止计费并终止该实例。失联期间产生的费用如有异议请联系客服。"
                                    "实例盘数据保留,释放实例前不会清除。"
                                ),
                                severity="error",
                                dedup_key=f"node_lost:{instance.id}",
                            )
                        else:
                            logger.error("instance_pod_lost", instance_id=instance.id)

                elif instance.status == sm_def.STOPPING:
                    if not st.exists:
                        await transition(
                            session,
                            instance,
                            sm_def.STOPPED,
                            reason="pod_deleted",
                            actor="system",
                        )
                        counts["to_stopped"] += 1

                elif instance.status == sm_def.RELEASING and not st.exists:
                    await transition(
                        session,
                        instance,
                        sm_def.RELEASED,
                        reason="released",
                        actor="system",
                        metadata={"disk_wipe": "blkdiscard"},
                    )
                    await free_port(session, instance.id)
                    await detach_for_instance(session, instance.id)
                    # 释放是实例盘唯一的销毁时点(用户复述实例名 + 勾选确认过)。
                    # Pod 已确认消失,PVC 不会被 pvc-protection 挂住。
                    await orch.delete_instance_disk(instance.k8s_namespace, instance.uuid)
                    counts["to_released"] += 1

                await session.commit()
        except Exception:
            logger.exception("reconcile_instance_failed", instance_id=instance_id)


async def _reclaim_leaked_pods(
    sm: async_sessionmaker[AsyncSession], counts: dict[str, int]
) -> None:
    """K8s 里存在、但 DB 已终态/已停止的 Pod → 强删。泄漏 = 白送算力。"""
    orch = get_orchestrator()
    pods = await orch.list_instance_pods()
    if not pods:
        return
    async with sm() as session:
        uuids = [name for _ns, name in pods]
        rows = (
            (
                await session.execute(
                    select(Instance.uuid, Instance.status).where(Instance.uuid.in_(uuids))
                )
            )
            .tuples()
            .all()
        )
        status_by_uuid: dict[str, str] = dict(rows)
    for ns, name in pods:
        db_status = status_by_uuid.get(name)
        # Pod 应该存在的状态:creating/starting/running(stopping/releasing 删除中,等 reconcile 边)
        if db_status in (sm_def.CREATING, sm_def.STARTING, sm_def.RUNNING):
            continue
        if db_status in (sm_def.STOPPING, sm_def.RELEASING):
            continue  # 删除任务在途
        logger.error("leaked_pod_reclaimed", namespace=ns, pod=name, db_status=db_status)
        RECONCILE_LEAKED_TOTAL.inc()
        await orch.delete_instance(ns, name)
        counts["leaked"] += 1
