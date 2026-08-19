"""reconciler:控制面正确性支柱二。每 30s 全量比对「DB 期望 ↔ K8s 实际」。

- creating/starting + Pod Ready → running(计费开始)
- creating/starting 超时未 Ready → failed(全额退=无账)+ 清理
- running + Pod 消失/异常 → failed(停止计费)+ 告警
- stopping + Pod 消失 → stopped(计费边,尾账监听器触发)
- releasing + Pod 消失 → released(擦盘事件 + 端口回收)
- K8s 存在但 DB 已终态的 Pod → 强制删除(泄漏 = 白送算力)
"""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.k8s import get_orchestrator
from app.core.locks import LockKey, try_advisory_lock
from app.core.logging import get_logger
from app.core.metrics import RECONCILE_LEAKED_TOTAL
from app.core.timeutil import now_utc
from app.modules.orchestrator import statemachine as sm_def
from app.modules.orchestrator.disks import detach_for_instance
from app.modules.orchestrator.models import Instance
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


async def _reconcile_instances(
    sm: async_sessionmaker[AsyncSession], counts: dict[str, int]
) -> None:
    settings = get_settings()
    orch = get_orchestrator()
    timeout = timedelta(seconds=settings.creating_timeout_seconds)

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
                assert instance.k8s_namespace is not None
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
                    elif now_utc() - instance.updated_at > timeout:
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
                        counts["to_failed"] += 1
                        logger.warning("instance_schedule_timeout", instance_id=instance.id)

                elif instance.status == sm_def.RUNNING:
                    if not st.exists or st.phase in ("Failed", "Succeeded"):
                        await transition(
                            session,
                            instance,
                            sm_def.FAILED,
                            reason="pod_lost",
                            actor="system",
                            metadata={"phase": st.phase if st.exists else "Missing"},
                        )
                        await free_port(session, instance.id)
                        await detach_for_instance(session, instance.id)
                        if st.exists:
                            await orch.delete_instance(instance.k8s_namespace, instance.uuid)
                        counts["to_failed"] += 1
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
