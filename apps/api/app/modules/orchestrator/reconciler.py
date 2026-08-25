"""reconciler:每 30s 全量比对「DB 期望 ↔ K8s 实际」并收敛。

- creating/starting + Pod Ready → running(计费开始)
- creating/starting 超时未 Ready → failed(全额退=无账)+ 清理
- running + Pod 消失/异常/持续 not-ready → failed(停止计费)+ 告警 + 通知用户
- stopping + Pod 消失 → stopped(计费边,尾账监听器触发)
- releasing + Pod 消失 → released(擦盘事件 + 端口回收)
- K8s 存在但 DB 已终态的 Pod → 超过宽限期后强删(清理泄漏;未知 Pod 占比超阈即熔断)
- 长期 stopped / failed 的实例盘保留期 GC(先预警,到期 releasing;数据盘不受影响)

K8s 读放大控制:每轮一次 list_instance_pods 即状态源(ready/phase/node_name/deleting),
不逐实例 get_status(单查会占满执行器线程,让建/删 Pod 排队)。

stopping/releasing 悬挂两档超时(默认各 10 分钟,宁宽勿严):
第一档经 outbox 重发删除任务,第二档 force=True 强删(失联节点上的优雅删除永远
完不成);强删后 Pod 从 etcd 消失,下一轮按正常边收敛(端口回池/实例盘销毁)。
"""

from datetime import timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.k8s import PodStatus, get_orchestrator
from app.core.k8s.base import JOB_NAME_LABEL, PodListEntry
from app.core.locks import LockKey, try_advisory_lock
from app.core.logging import get_logger
from app.core.metrics import (
    INSTANCE_NODE_LOST_TOTAL,
    RECONCILE_LEAK_ABORTED_TOTAL,
    RECONCILE_LEAKED_TOTAL,
    RECONCILE_STUCK_INSTANCES,
)
from app.core.outbox import RUNNING_TIMEOUT, OutboxTask, enqueue
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
    counts = {
        "to_running": 0,
        "to_failed": 0,
        "to_stopped": 0,
        "to_released": 0,
        "leaked": 0,
        "job_pod_skipped": 0,
        "delete_requeued": 0,
        "force_deleted": 0,
        "gc_warned": 0,
        "gc_released": 0,
        "ports_unblocked": 0,
        "wipe_redriven": 0,
        "quota_redriven": 0,
    }
    async with sm() as lock_session, try_advisory_lock(lock_session, LockKey.RECONCILER) as got:
        if not got:
            return counts
        await _reconcile_instances(sm, counts)
        await _reclaim_leaked_pods(sm, counts)
        await _recheck_blocked_ports(sm, counts)
        await _redrive_dead_disk_wipes(sm, counts)
        await _reconcile_disk_quotas(sm, counts)
        await _gc_retention(sm, counts)
    return counts


async def _entered_status_at(session: AsyncSession, instance: Instance):
    """实例进入当前状态的时刻(取该状态最后一条事件)。

    不用 updated_at:它带 onupdate,handler 回填任何字段都会重置计时。
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


async def _entered_status_map(session: AsyncSession, instances: list[Instance]) -> dict[int, Any]:
    """批量版 _entered_status_at:一次分组查询拿全部实例进入当前状态的时刻。"""
    if not instances:
        return {}
    pairs = {(i.id, i.status) for i in instances}
    rows = (
        await session.execute(
            select(
                InstanceEvent.instance_id,
                InstanceEvent.to_status,
                func.max(InstanceEvent.created_at),
            )
            .where(
                InstanceEvent.instance_id.in_([i.id for i in instances]),
                InstanceEvent.to_status.in_([s for _i, s in pairs]),
            )
            .group_by(InstanceEvent.instance_id, InstanceEvent.to_status)
        )
    ).all()
    latest = {(iid, to): ensure_utc(ts) for iid, to, ts in rows}
    return {i.id: latest.get((i.id, i.status), ensure_utc(i.created_at)) for i in instances}


async def _running_pod_lost_reason(
    session: AsyncSession,
    instance: Instance,
    st: PodStatus,
    unready_timeout: timedelta,
    node_not_ready: bool | None,
) -> str | None:
    """running 实例是否已经不可用了。返回迁移 reason,None = 还活着。

    节点失联时 phase 仍是 Running、对象仍在 etcd,只有 Ready condition 转 False,
    故 exists 与 phase 之外还要看 ready。持续 not-ready 超宽限后按节点 Ready 状况
    分流:节点也失联 → node_lost(通知用户);节点正常 → pod_unready(Pod 自身问题)。
    node_not_ready=None 表示节点视图本轮不可用,回落旧口径(按失联处理)。
    """
    if not st.exists or st.phase in ("Failed", "Succeeded"):
        return "pod_lost"
    if st.deleting:
        return "pod_lost"  # 被驱逐/被外部删除:running 态的删除一定不是我们发起的
    if st.ready:
        return None
    # not-ready 给一段宽限,容忍容器重启、镜像层重挂这类抖动
    if instance.unready_since is None:
        instance.unready_since = now_utc()
        await session.flush()
        return None
    if now_utc() - ensure_utc(instance.unready_since) > unready_timeout:
        return "node_lost" if node_not_ready is not False else "pod_unready"
    return None


async def _reenqueue_delete(session: AsyncSession, task_type: str, instance_id: int) -> bool:
    """悬挂恢复第一档:重发删除任务。已有在途同型任务则跳过(不堆重复任务)。

    在途判定:running 行仅在 locked_at 租约未超 RUNNING_TIMEOUT 时算在途;
    租约过期 = 执行 worker 已死(终态写按 locked_by 校验,旧副本结果会被丢弃),
    等 reaper(5 分钟一轮)打回 pending 之前,这里直接补发新任务让收敛不等拍。
    """
    pending = (
        await session.execute(
            select(func.count())
            .select_from(OutboxTask)
            .where(
                OutboxTask.type == task_type,
                OutboxTask.payload["instance_id"].as_string() == str(instance_id),
                or_(
                    OutboxTask.status == "pending",
                    and_(
                        OutboxTask.status == "running",
                        OutboxTask.locked_at >= now_utc() - RUNNING_TIMEOUT,
                    ),
                ),
            )
        )
    ).scalar_one()
    if pending:
        return False
    enqueue(session, task_type, {"instance_id": instance_id})
    return True


def _statuses_from_listing(
    rows: list[tuple[int, str, str, str]], listing: list[PodListEntry]
) -> list[PodStatus]:
    """全量 LIST 即状态源:与 get_status 同口径(ready/phase/node_name/deleting),
    逐实例单查(有界并发 8)在对账高峰会占满 RealOrchestrator 的 8 线程执行器,
    让建/删 Pod 排队——N 次 GET 降为 0。stopping/releasing 只需存在性。"""
    by_key = {(e.namespace, e.name): e for e in listing}
    out: list[PodStatus] = []
    for _id, status, ns, uuid in rows:
        entry = by_key.get((ns, uuid))
        if entry is None:
            out.append(PodStatus(exists=False))
        elif status in (sm_def.STOPPING, sm_def.RELEASING):
            out.append(PodStatus(exists=True))
        else:
            out.append(
                PodStatus(
                    exists=True,
                    ready=entry.ready,
                    phase=entry.phase,
                    node_name=entry.node_name,
                    deleting=entry.deleting,
                )
            )
    return out


async def _node_readiness() -> dict[str, bool] | None:
    """节点 → 是否 NotReady。本轮节点视图不可用返回 None(node_lost 判定回落旧口径)。"""
    try:
        nodes = await get_orchestrator().list_nodes()
    except Exception:
        logger.exception("reconcile_list_nodes_failed")
        return None
    return {n.name: n.status != "Ready" for n in nodes}


async def _reconcile_instances(
    sm: async_sessionmaker[AsyncSession], counts: dict[str, int]
) -> None:
    settings = get_settings()
    orch = get_orchestrator()
    timeout = timedelta(seconds=settings.creating_timeout_seconds)
    unready_timeout = timedelta(seconds=settings.running_unready_timeout_seconds)
    stop_timeout = timedelta(seconds=settings.stopping_timeout_seconds)
    release_timeout = timedelta(seconds=settings.releasing_timeout_seconds)

    async with sm() as session:
        rows = list(
            (
                await session.execute(
                    select(
                        Instance.id, Instance.status, Instance.k8s_namespace, Instance.uuid
                    ).where(Instance.status.in_(ACTIVE_STATUSES))
                )
            )
            .tuples()
            .all()
        )
    if not rows:
        RECONCILE_STUCK_INSTANCES.labels(status=sm_def.STOPPING).set(0)
        RECONCILE_STUCK_INSTANCES.labels(status=sm_def.RELEASING).set(0)
        return
    try:
        # 一次全量 LIST 建状态索引(替代每实例一次 get_status 的读放大)
        listing = await orch.list_instance_pods()
    except Exception:
        # 拿不到状态索引宁可本轮全跳过:误判「Pod 消失」会把健康实例打成 failed
        logger.exception("reconcile_list_pods_failed")
        return
    statuses = _statuses_from_listing(rows, listing)
    not_ready_by_node = await _node_readiness()
    stuck = {sm_def.STOPPING: 0, sm_def.RELEASING: 0}

    for (instance_id, row_status, _ns, _uuid), st in zip(rows, statuses, strict=True):
        # 每实例独立事务:单个失败不拖垮整轮
        try:
            # K8s 清理动作在 commit 后执行(P1-9 两阶段):事务内只做状态迁移/标记/enqueue,
            # DB 行锁不跨 K8s RT(读超时 30s);动作失败仅记日志,重试/兜底语义见各分支
            post_commit: list[tuple[str, Any]] = []
            async with sm() as session:
                instance = await session.get(Instance, instance_id)
                if instance is None or instance.status != row_status:
                    continue
                if instance.status not in ACTIVE_STATUSES:
                    continue

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
                        # delete_instance 404 容错:顺带清可能残留的 Service/Ingress(防孤儿端点
                        # 占 NodePort)。事务外执行;失败由泄漏回收宽限期后强删兜底
                        ns, uuid = instance.k8s_namespace, instance.uuid
                        post_commit.append(
                            (
                                "delete_instance",
                                lambda ns=ns, uuid=uuid: orch.delete_instance(ns, uuid),
                            )
                        )
                        if first_boot:
                            # creating 超时的盘从未承载数据,回收不留孤儿 LV;
                            # starting 超时不删盘(实例停过机,盘里有上一轮数据)。
                            # 统一交 outbox:handler 等 Pod 消失再删(pvc-protection 语义)
                            enqueue(
                                session,
                                "instance.disk_cleanup",
                                {"instance_id": instance.id},
                            )
                        counts["to_failed"] += 1
                        logger.warning("instance_schedule_timeout", instance_id=instance.id)
                        # 对齐 node_lost:创建失败必须主动告知(未计费),不是等用户刷新发现
                        await notify_service.notify(
                            session,
                            instance.user_id,
                            type_="instance",
                            title="实例创建失败:调度超时",
                            content=(
                                f"实例「{instance.name}」调度或镜像拉取超时,已自动终止,"
                                "未产生任何费用。可换个档位重试,或稍后再试;"
                                "多次失败请联系客服。"
                            ),
                            severity="warning",
                            dedup_key=f"schedule_timeout:{instance.id}",
                        )

                elif instance.status == sm_def.RUNNING:
                    node_not_ready = (
                        None
                        if not_ready_by_node is None or instance.node_name is None
                        else not_ready_by_node.get(instance.node_name)
                    )
                    lost = await _running_pod_lost_reason(
                        session, instance, st, unready_timeout, node_not_ready
                    )
                    if lost is None:
                        if instance.unready_since is not None:
                            instance.unready_since = None  # 抖动恢复,重新计时
                    else:
                        # 平台责任失联(node_lost/pod_lost):unready_since 写进事件 metadata,
                        # 计费据此截断到 Pod 首次不可用时点;pod_unready 不截断,照常计费
                        meta: dict[str, Any] = {
                            "phase": st.phase if st.exists else "Missing",
                            "ready": st.ready,
                        }
                        if lost in ("node_lost", "pod_lost") and instance.unready_since is not None:
                            meta["unready_since"] = ensure_utc(instance.unready_since).isoformat()
                        await transition(
                            session,
                            instance,
                            sm_def.FAILED,
                            reason=lost,
                            actor="system",
                            metadata=meta,
                        )
                        await free_port(session, instance.id)
                        await detach_for_instance(session, instance.id)
                        # 失联节点上的 Pod 只有强删才会从 etcd 消失;404 容错,连带清孤儿端点。
                        # 事务外执行(P1-9);失败由泄漏回收宽限期后强删兜底
                        ns, uuid = instance.k8s_namespace, instance.uuid
                        force = lost == "node_lost"
                        post_commit.append(
                            (
                                "delete_instance",
                                lambda ns=ns, uuid=uuid, force=force: orch.delete_instance(
                                    ns, uuid, force=force
                                ),
                            )
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
                                    "实例盘为该节点本地盘,平台不做冗余:节点恢复前该实例暂不可开机,"
                                    "若节点最终无法恢复,盘中数据将无法找回——重要数据请务必自行备份到数据盘或站外。"
                                ),
                                severity="error",
                                dedup_key=f"node_lost:{instance.id}",
                            )
                        else:
                            logger.error("instance_pod_lost", instance_id=instance.id, reason=lost)

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
                    else:
                        age = now_utc() - await _entered_status_at(session, instance)
                        if age > stop_timeout * 2:
                            # 事务外强删(P1-9):实例仍 STOPPING(ACTIVE),失败下轮本分支重试
                            ns, uuid = instance.k8s_namespace, instance.uuid

                            async def _force_stop(
                                ns=ns, uuid=uuid, iid=instance.id, age_s=int(age.total_seconds())
                            ):
                                await orch.delete_instance(ns, uuid, force=True)
                                counts["force_deleted"] += 1
                                logger.error(
                                    "stopping_force_deleted",
                                    instance_id=iid,
                                    age_seconds=age_s,
                                )

                            post_commit.append(("force_delete_instance", _force_stop))
                        elif age > stop_timeout:
                            stuck[sm_def.STOPPING] += 1
                            if await _reenqueue_delete(session, "instance.stop", instance.id):
                                counts["delete_requeued"] += 1
                            logger.warning(
                                "stopping_stuck_requeued",
                                instance_id=instance.id,
                                age_seconds=int(age.total_seconds()),
                            )

                elif instance.status == sm_def.RELEASING:
                    if not st.exists:
                        await transition(
                            session,
                            instance,
                            sm_def.RELEASED,
                            reason="released",
                            actor="system",
                            metadata={"disk_wipe": "lvremove(issue_discards=1)"},
                        )
                        await free_port(session, instance.id)
                        await detach_for_instance(session, instance.id)
                        # 释放是实例盘唯一的销毁时点:统一交 outbox(handler 等 Pod 消失再删,
                        # at-least-once + 死信重派兜底,P1-9 事务内零 K8s 调用)
                        enqueue(session, "instance.disk_cleanup", {"instance_id": instance.id})
                        counts["to_released"] += 1
                    else:
                        age = now_utc() - await _entered_status_at(session, instance)
                        if age > release_timeout * 2:
                            # 事务外强删(P1-9):实例仍 RELEASING(ACTIVE),失败下轮本分支重试
                            ns, uuid = instance.k8s_namespace, instance.uuid

                            async def _force_release(
                                ns=ns, uuid=uuid, iid=instance.id, age_s=int(age.total_seconds())
                            ):
                                await orch.delete_instance(ns, uuid, force=True)
                                counts["force_deleted"] += 1
                                logger.error(
                                    "releasing_force_deleted",
                                    instance_id=iid,
                                    age_seconds=age_s,
                                )

                            post_commit.append(("force_delete_instance", _force_release))
                        elif age > release_timeout:
                            stuck[sm_def.RELEASING] += 1
                            if await _reenqueue_delete(session, "instance.release", instance.id):
                                counts["delete_requeued"] += 1
                            logger.warning(
                                "releasing_stuck_requeued",
                                instance_id=instance.id,
                                age_seconds=int(age.total_seconds()),
                            )

                await session.commit()
            # 事务已提交(状态迁移/标记/enqueue 落库):K8s 清理在锁外执行。
            # 失败仅记日志不中断:FAILED/RELEASED 实例的 Pod 残留由泄漏回收兜底,
            # STOPPING/RELEASING 的强删失败下轮同分支重试,盘删除走 outbox 重派
            for action_label, action in post_commit:
                try:
                    await action()
                except Exception:
                    logger.exception(
                        "reconcile_k8s_cleanup_failed",
                        action=action_label,
                        instance_id=instance_id,
                    )
        except Exception:
            logger.exception("reconcile_instance_failed", instance_id=instance_id)

    RECONCILE_STUCK_INSTANCES.labels(status=sm_def.STOPPING).set(stuck[sm_def.STOPPING])
    RECONCILE_STUCK_INSTANCES.labels(status=sm_def.RELEASING).set(stuck[sm_def.RELEASING])


async def _reclaim_leaked_pods(
    sm: async_sessionmaker[AsyncSession], counts: dict[str, int]
) -> None:
    """K8s 里存在、但 DB 已终态/无记录的 Pod → 强删(宽限期内的在途删除不动)。

    强删(force=True)是有意的:泄漏 Pod 在白送算力,失联节点上优雅删除永远完不成。
    豁免:带 batch.kubernetes.io/job-name 标签的 Pod 是受管 Job(wipe/quota)的子孙,
    名字不是实例 uuid、DB 必然无记录——误删会让擦盘「建-杀死」循环;Job 泄漏由
    ttl_seconds_after_finished=3600 兜底,不属本函数职责。
    熔断:未知(DB 无记录、且非 Job 子孙)Pod 占比超阈,说明 LIST 结果与 DB 大面积
    不一致(接错集群/标签漂移),中止本轮并告警,而不是按陌生对象清单批量强删。
    """
    settings = get_settings()
    orch = get_orchestrator()
    entries = await orch.list_instance_pods()
    pods = [(e.namespace, e.name, e.labels) for e in entries]
    async with sm() as session:
        uuids = [name for _ns, name, _labels in pods]
        instances = (
            list(
                (await session.execute(select(Instance).where(Instance.uuid.in_(uuids)))).scalars()
            )
            if uuids
            else []
        )
        by_uuid = {i.uuid: i for i in instances}
        # 宽限判定需要「进入状态时刻」:覆盖全部非活跃状态(stopping/releasing 在途删除,
        # stopped/failed 与 restart 建 Pod 窗口),活跃态(creating/starting/running)直接放行
        in_flight = [
            i
            for i in instances
            if i.status not in (sm_def.CREATING, sm_def.STARTING, sm_def.RUNNING)
        ]
        entered_at = await _entered_status_map(session, in_flight)

    if pods:
        # Job 子孙 Pod 既不计入 unknown 分子也不计入分母:单租户删盘场景下
        # wipe Pod 会把 unknown 占比推向熔断线,反而放跑真泄漏 Pod
        managed = [p for p in pods if JOB_NAME_LABEL not in p[2]]
        unknown = [p for p in managed if p[1] not in by_uuid]
        if unknown and len(unknown) / len(managed) > settings.leak_reclaim_abort_ratio:
            RECONCILE_LEAK_ABORTED_TOTAL.inc()
            logger.error(
                "leak_reclaim_aborted",
                total=len(managed),
                unknown=len(unknown),
                ratio=settings.leak_reclaim_abort_ratio,
            )
            return

    stop_grace = timedelta(seconds=settings.stopping_timeout_seconds) * 2
    release_grace = timedelta(seconds=settings.releasing_timeout_seconds) * 2
    for ns, name, labels in pods:
        if JOB_NAME_LABEL in labels:
            counts["job_pod_skipped"] += 1
            continue
        instance = by_uuid.get(name)
        db_status = instance.status if instance is not None else None
        # Pod 应该存在的状态:creating/starting/running
        if db_status in (sm_def.CREATING, sm_def.STARTING, sm_def.RUNNING):
            continue
        if db_status in (sm_def.STOPPING, sm_def.RELEASING, sm_def.STOPPED, sm_def.FAILED):
            # 在途删除与 restart 建 Pod 窗口(DB stopped/failed、Pod 已建)都给宽限;
            # 超时由本函数强删(与主对账同阈值)
            grace = release_grace if db_status == sm_def.RELEASING else stop_grace
            age = now_utc() - entered_at.get(instance.id, ensure_utc(instance.created_at))  # type: ignore[union-attr]
            if age <= grace:
                continue
        logger.error("leaked_pod_reclaimed", namespace=ns, pod=name, db_status=db_status)
        RECONCILE_LEAKED_TOTAL.inc()
        await orch.delete_instance(ns, name, force=True)
        counts["leaked"] += 1

    # 孤儿 Service/Ingress(Pod 已消失但端点残留)会继续占 NodePort,同一熔断比例下清理
    try:
        endpoints = await orch.list_instance_endpoints()
    except Exception:
        logger.exception("reconcile_list_endpoints_failed")
        return
    if not endpoints:
        return
    async with sm() as session:
        ep_instances = list(
            (
                await session.execute(
                    select(Instance).where(Instance.uuid.in_([n for _ns, n in endpoints]))
                )
            ).scalars()
        )
        ep_by_uuid = {i.uuid: i for i in ep_instances}
        ep_terminal = [i for i in ep_instances if i.status not in ACTIVE_STATUSES]
        ep_entered = await _entered_status_map(session, ep_terminal)
    unknown_ep = [e for e in endpoints if e[1] not in ep_by_uuid]
    if unknown_ep and len(unknown_ep) / len(endpoints) > settings.leak_reclaim_abort_ratio:
        RECONCILE_LEAK_ABORTED_TOTAL.inc()
        logger.error("endpoint_reclaim_aborted", total=len(endpoints), unknown=len(unknown_ep))
        return
    for ns, name in endpoints:
        inst = ep_by_uuid.get(name)
        if inst is not None and inst.status in ACTIVE_STATUSES:
            continue
        if inst is not None:
            age = now_utc() - ep_entered.get(inst.id, ensure_utc(inst.created_at))
            if age <= stop_grace:
                continue
        logger.error(
            "leaked_endpoint_reclaimed",
            namespace=ns,
            name=name,
            db_status=inst.status if inst is not None else None,
        )
        RECONCILE_LEAKED_TOTAL.inc()
        await orch.delete_instance(ns, name, force=True)  # 404 容错,幂等清端点
        counts["leaked"] += 1


async def _recheck_blocked_ports(
    sm: async_sessionmaker[AsyncSession], counts: dict[str, int]
) -> None:
    """blocked 端口周期复检:集群侧占用已消失(孤儿端点已清)即放回池,防单向蚕食。"""
    from app.modules.orchestrator.models import PortAllocation

    orch = get_orchestrator()
    try:
        used = await orch.used_node_ports()
    except Exception:
        logger.exception("recheck_blocked_ports_failed")
        return
    async with sm() as session:
        blocked = list(
            (
                await session.execute(
                    select(PortAllocation).where(PortAllocation.blocked.is_(True))
                )
            ).scalars()
        )
        recovered = [row for row in blocked if row.port not in used]
        for row in recovered:
            row.blocked = False
        if recovered:
            await session.commit()
            logger.info("blocked_ports_recovered", count=len(recovered))
    counts["ports_unblocked"] = len(recovered)


async def _redrive_dead_disk_wipes(
    sm: async_sessionmaker[AsyncSession], counts: dict[str, int]
) -> None:
    """disk.wipe 死信重派:预算耗尽后没有收敛环,JuiceFS 子目录永不擦除(合规/留存风险)。
    死信行保留给管理端审计,这里只补发新任务(无在途同盘任务时)。"""
    from app.modules.orchestrator.models import DataDisk

    async with sm() as session:
        dead = list(
            (
                await session.execute(
                    select(OutboxTask).where(
                        OutboxTask.type == "disk.wipe", OutboxTask.status == "dead"
                    )
                )
            ).scalars()
        )
        redriven = 0
        for task in dead:
            disk_id = int(task.payload["disk_id"])
            disk = await session.get(DataDisk, disk_id)
            if disk is None or disk.status != "deleting":
                continue
            in_flight = (
                await session.execute(
                    select(func.count())
                    .select_from(OutboxTask)
                    .where(
                        OutboxTask.type == "disk.wipe",
                        OutboxTask.status.in_(("pending", "running")),
                        OutboxTask.payload["disk_id"].as_string() == str(disk_id),
                    )
                )
            ).scalar_one()
            if in_flight:
                continue
            enqueue(session, "disk.wipe", {"disk_id": disk_id})
            redriven += 1
        if redriven:
            await session.commit()
            logger.warning("disk_wipe_redriven", count=redriven)
    counts["wipe_redriven"] = redriven


async def _reconcile_disk_quotas(
    sm: async_sessionmaker[AsyncSession], counts: dict[str, int]
) -> None:
    """disk.quota 死信超 1 小时重派并计指标(配额未强制是计费完整性与防滥用缺口)。

    只看死信,不按 quota_synced=false 补发:配额任务与建盘/扩容同事务入队,不存在漏网盘;
    按标记补发会把管理端人工 discarded 的死信每轮复活,人工忽略即失效。
    """
    from app.core.metrics import JUICEFS_QUOTA_FAILED_TOTAL
    from app.modules.orchestrator.models import DataDisk

    async with sm() as session:
        in_flight_ids = {
            int(r[0])
            for r in (
                await session.execute(
                    select(OutboxTask.payload["disk_id"].as_string()).where(
                        OutboxTask.type == "disk.quota",
                        OutboxTask.status.in_(("pending", "running")),
                    )
                )
            ).all()
            if r[0] and r[0].isdigit()
        }
        redriven = 0
        dead_cutoff = now_utc() - timedelta(hours=1)
        dead = list(
            (
                await session.execute(
                    select(OutboxTask).where(
                        OutboxTask.type == "disk.quota",
                        OutboxTask.status == "dead",
                        OutboxTask.updated_at < dead_cutoff,
                    )
                )
            ).scalars()
        )
        for task in dead:
            disk_id = int(task.payload["disk_id"])
            disk = await session.get(DataDisk, disk_id)
            if disk is None or disk.status in ("deleting", "deleted") or disk.quota_synced:
                continue
            if disk_id in in_flight_ids:
                continue
            enqueue(session, "disk.quota", {"disk_id": disk_id})
            JUICEFS_QUOTA_FAILED_TOTAL.inc()
            redriven += 1
        if redriven:
            await session.commit()
            logger.warning("disk_quota_redriven", count=redriven)
    counts["quota_redriven"] = redriven


async def _gc_retention(sm: async_sessionmaker[AsyncSession], counts: dict[str, int]) -> None:
    """长期 stopped / failed 实例的实例盘保留期 GC(有偿用户的停机盘不再无限免费占用)。

    stopped:先预警(保留期 - stopped_retention_warn_days)再转 releasing;
    failed:直接到期转 releasing(实例盘从未被收费但也留不住,配额外的泄漏由此收口)。
    两条路径都经 outbox 走正常释放链路(数据盘不受影响)。
    """
    settings = get_settings()
    now = now_utc()
    stop_after = timedelta(days=settings.stopped_retention_days)
    warn_after = stop_after - timedelta(days=settings.stopped_retention_warn_days)
    fail_after = timedelta(days=settings.failed_retention_days)
    # 年龄条件下推到 SQL:进入当前状态的时刻不早于创建时刻,created_at 比最小阈值
    # 还新的行不可能到期(超集过滤,不漏不错);保留期内的行不每 30s 全量拉
    oldest_relevant = now - min(warn_after, fail_after)
    async with sm() as session:
        instances = list(
            (
                await session.execute(
                    select(Instance)
                    .where(Instance.status.in_((sm_def.STOPPED, sm_def.FAILED)))
                    .where(Instance.created_at < oldest_relevant)
                )
            ).scalars()
        )
        entered_at = await _entered_status_map(session, instances)

    for instance in instances:
        age = now - entered_at.get(instance.id, ensure_utc(instance.created_at))
        try:
            if instance.status == sm_def.FAILED and age > fail_after:
                async with sm() as session:
                    fresh = await session.get(Instance, instance.id)
                    if fresh is None or fresh.status != sm_def.FAILED:
                        continue
                    await transition(
                        session,
                        fresh,
                        sm_def.RELEASING,
                        reason="failed_retention_reclaim",
                        actor="system",
                        metadata={"hint": "失败实例超过保留期,自动释放"},
                    )
                    enqueue(session, "instance.release", {"instance_id": fresh.id})
                    await notify_service.notify(
                        session,
                        fresh.user_id,
                        type_="instance",
                        title="失败实例已自动释放",
                        content=(
                            f"实例「{fresh.name}」启动失败后超过 "
                            f"{settings.failed_retention_days} 天未处理,已自动释放"
                            "(实例盘清除,数据盘不受影响)。"
                        ),
                        severity="warning",
                        dedup_key=f"failed_retention:{fresh.id}",
                    )
                    await session.commit()
                    counts["gc_released"] += 1
            elif instance.status == sm_def.STOPPED and age > stop_after:
                async with sm() as session:
                    fresh = await session.get(Instance, instance.id)
                    if fresh is None or fresh.status != sm_def.STOPPED:
                        continue
                    await transition(
                        session,
                        fresh,
                        sm_def.RELEASING,
                        reason="retention_reclaim",
                        actor="system",
                        metadata={"hint": "停机超过保留期,自动释放"},
                    )
                    enqueue(session, "instance.release", {"instance_id": fresh.id})
                    await notify_service.notify(
                        session,
                        fresh.user_id,
                        type_="instance",
                        title="停机实例已自动释放",
                        content=(
                            f"实例「{fresh.name}」已停机超过 "
                            f"{settings.stopped_retention_days} 天,按保留期策略自动释放"
                            "(实例盘清除,数据盘不受影响)。"
                        ),
                        severity="warning",
                        dedup_key=f"retention_reclaim:{fresh.id}",
                    )
                    await session.commit()
                    counts["gc_released"] += 1
            elif instance.status == sm_def.STOPPED and age > warn_after:
                warn_days = settings.stopped_retention_days - settings.stopped_retention_warn_days
                async with sm() as session:
                    await notify_service.notify(
                        session,
                        instance.user_id,
                        type_="instance",
                        title="停机实例即将到期释放",
                        content=(
                            f"实例「{instance.name}」已停机超过 {warn_days} 天;"
                            f"停机满 {settings.stopped_retention_days} 天将自动释放"
                            "(实例盘清除,数据盘不受影响)。如需保留请开机或备份后释放。"
                        ),
                        severity="warning",
                        dedup_key=f"retention_warn:{instance.id}",
                    )
                    await session.commit()
                    counts["gc_warned"] += 1
        except Exception:
            logger.exception("gc_retention_failed", instance_id=instance.id)
