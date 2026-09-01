"""reconciler:每 30s 全量比对「DB 期望 ↔ K8s 实际」并收敛。

- creating/starting + Pod Ready → running(计费开始)
- creating/starting 超时未 Ready → failed(全额退=无账)+ 清理
- running + Pod 消失/异常/持续 not-ready → failed(停止计费)+ 告警 + 通知用户
- stopping + Pod 消失 → stopped(计费边,尾账监听器触发)
- releasing + Pod 消失 → released(擦盘事件 + 端口回收)
- K8s 存在但 DB 已终态的 Pod → 超过宽限期后强删(清理泄漏;未知 Pod 占比超阈即熔断)
- 长期 stopped / failed 的实例盘保留期 GC(先预警,到期 releasing;数据盘不受影响)

K8s 读放大控制:每轮一次 list_instance_pods 即状态源(ready/phase/node_name/deleting),
不逐实例 get_status —— 单查会占满执行器线程,让建/删 Pod 排队。

stopping/releasing 悬挂两档超时:一档经 outbox 重发删除任务,二档 force=True 强删
(失联节点上的优雅删除永远完不成);强删后下一轮按正常边收敛(端口回池/实例盘销毁)。
"""

from collections.abc import Iterable
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.k8s import PodStatus, get_orchestrator
from app.core.k8s.base import JOB_NAME_LABEL
from app.core.locks import LockKey, advisory_lock
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
    async with advisory_lock(sm, LockKey.RECONCILER) as got:
        if not got:
            return counts
        await _reconcile_instances(sm, counts)
        await _reclaim_leaked_pods(sm, counts)
        await _recheck_blocked_ports(sm, counts)
        await _redrive_dead_disk_wipes(sm, counts)
        await _reconcile_disk_quotas(sm, counts)
        await _gc_retention(sm, counts)
    return counts


def _status_keys(instances: Iterable[Instance]) -> list[tuple[int, str, Any]]:
    """_entered_status_map 的输入形态:(instance_id, 当前状态, created_at)。"""
    return [(i.id, i.status, i.created_at) for i in instances]


async def _entered_status_map(
    session: AsyncSession, keys: Iterable[tuple[int, str, Any]]
) -> dict[int, Any]:
    """批量取「进入当前状态的时刻」:instance_id → 该状态最后一条事件的时刻,一次分组查询。

    不用 updated_at:它带 onupdate,handler 回填任何字段都会重置计时。
    无对应事件的行(直插的测试数据)回落 created_at。
    """
    keys = list(keys)
    if not keys:
        return {}
    rows = (
        await session.execute(
            select(
                InstanceEvent.instance_id,
                InstanceEvent.to_status,
                func.max(InstanceEvent.created_at),
            )
            .where(
                InstanceEvent.instance_id.in_([iid for iid, _s, _c in keys]),
                InstanceEvent.to_status.in_({status for _i, status, _c in keys}),
            )
            .group_by(InstanceEvent.instance_id, InstanceEvent.to_status)
        )
    ).all()
    latest = {(iid, to): ensure_utc(ts) for iid, to, ts in rows}
    return {iid: latest.get((iid, status), ensure_utc(created)) for iid, status, created in keys}


async def _running_pod_lost_reason(
    session: AsyncSession,
    instance: Instance,
    st: PodStatus,
    unready_timeout: timedelta,
    node_not_ready: bool | None,
) -> str | None:
    """running 实例是否已经不可用了。返回迁移 reason,None = 还活着。

    节点失联时 phase 仍是 Running、对象仍在 etcd,只有 Ready condition 转 False,
    故 exists 与 phase 之外还要看 ready。持续 not-ready 超宽限后按节点 Ready 状况分流:
    节点也失联 → node_lost(通知用户),节点正常 → pod_unready;node_not_ready=None
    表示节点视图本轮不可用,按失联处理。

    服务型实例不走 pod_unready 这一支:它的 not-ready 判据是用户自己声明的 readinessProbe,
    长期不过属用户容器问题,判 failed 会把一台付费实例误标成故障。

    unready_since 的清零只有 st.ready 这一处:返回 None 不等于「恢复了」——宽限期内、
    以及服务型实例的 pod_unready 豁免都返回 None,由调用方按 None 清零会把计时器每轮
    抹平,超时分支永不可达(Pod 卡在 Running-not-ready 就永远不判故障、一直计费)。
    """
    if not st.exists or st.phase in ("Failed", "Succeeded"):
        return "pod_lost"
    if st.deleting:
        return "pod_lost"  # 被驱逐/被外部删除:running 态的删除一定不是我们发起的
    if st.ready:
        if instance.unready_since is not None:
            instance.unready_since = None  # 抖动恢复,重新计时
            await session.flush()
        return None
    # not-ready 给一段宽限,容忍容器重启、镜像层重挂这类抖动
    if instance.unready_since is None:
        instance.unready_since = now_utc()
        await session.flush()
        return None
    if now_utc() - ensure_utc(instance.unready_since) > unready_timeout:
        if node_not_ready is not False:
            return "node_lost"
        return "pod_unready" if instance.workload_type != "service" else None
    return None


async def _reenqueue_delete(session: AsyncSession, task_type: str, instance_id: int) -> bool:
    """悬挂恢复第一档:重发删除任务。已有在途同型任务则跳过(不堆重复任务)。

    在途判定:running 行仅在 locked_at 租约未超 RUNNING_TIMEOUT 时算在途;租约过期即
    执行 worker 已死(终态写按 locked_by 校验),不等 reaper 打回 pending 就补发新任务。
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


async def _escalate_stuck(
    session: AsyncSession,
    instance: Instance,
    *,
    age: timedelta,
    stuck_after: timedelta,
    task_type: str,
    counts: dict[str, int],
    stuck: dict[str, int],
    post_commit: list[tuple[str, Any]],
) -> None:
    """stopping/releasing 悬挂两档:一档(> stuck_after)经 outbox 重发删除任务;
    二档(> 2×stuck_after)事务外 force 强删(实例仍在 ACTIVE,强删失败下轮本分支重试)。"""
    status = instance.status
    age_s = int(age.total_seconds())
    if age > stuck_after * 2:
        orch = get_orchestrator()
        ns, uuid, iid = instance.k8s_namespace, instance.uuid, instance.id

        async def _force_delete() -> None:
            await orch.delete_instance(ns, uuid, force=True)
            counts["force_deleted"] += 1
            logger.error(f"{status}_force_deleted", instance_id=iid, age_seconds=age_s)

        post_commit.append(("force_delete_instance", _force_delete))
    elif age > stuck_after:
        stuck[status] += 1
        if await _reenqueue_delete(session, task_type, instance.id):
            counts["delete_requeued"] += 1
        logger.warning(f"{status}_stuck_requeued", instance_id=instance.id, age_seconds=age_s)


_MISSING_POD = PodStatus(exists=False)


def _statuses_from_listing(
    rows: list[tuple[int, str, str, str, Any]], listing: list[PodStatus]
) -> list[PodStatus]:
    """全量 LIST 即状态源,与 get_status 同口径(ready/phase/node_name/deleting)。
    逐实例单查会占满 RealOrchestrator 的线程执行器,让建/删 Pod 排队。"""
    by_key = {(e.namespace, e.name): e for e in listing}
    return [by_key.get((ns, uuid), _MISSING_POD) for _id, _status, ns, uuid, _created in rows]


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
                        Instance.id,
                        Instance.status,
                        Instance.k8s_namespace,
                        Instance.uuid,
                        Instance.created_at,
                    ).where(Instance.status.in_(ACTIVE_STATUSES))
                )
            )
            .tuples()
            .all()
        )
        # 超时/悬挂判定的基准时刻一次批量算好;逐实例事务里状态一变即跳过,不会用到过期值
        entered_at = await _entered_status_map(
            session, [(iid, status, created) for iid, status, _ns, _uuid, created in rows]
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

    for (instance_id, row_status, _ns, _uuid, _created), st in zip(rows, statuses, strict=True):
        # 每实例独立事务:单个失败不拖垮整轮
        try:
            # K8s 清理动作必须在 commit 后执行:事务内只做状态迁移/标记/enqueue,
            # DB 行锁不跨 K8s RT;动作失败仅记日志,重试/兜底语义见各分支
            post_commit: list[tuple[str, Any]] = []
            async with sm() as session:
                instance = await session.get(Instance, instance_id)
                if instance is None or instance.status != row_status:
                    continue

                if instance.status in (sm_def.CREATING, sm_def.STARTING):
                    ready = st.exists and st.ready
                    # 端口就位只对开了 SSH 的实例有意义:服务型实例不进端口池,拿「端口非空」
                    # 当推进 running 的前置会让它停在 creating 直到超时转 failed
                    port_ok = instance.ssh_port is not None or not instance.with_ssh
                    if ready and port_ok:
                        instance.node_name = st.node_name
                        await transition(
                            session,
                            instance,
                            sm_def.RUNNING,
                            reason="pod_ready",
                            actor="system",
                        )
                        counts["to_running"] += 1
                    elif now_utc() - entered_at[instance_id] > timeout:
                        first_boot = instance.status == sm_def.CREATING
                        await transition(
                            session,
                            instance,
                            sm_def.FAILED,
                            reason="schedule_timeout",
                            actor="system",
                        )
                        await free_port(session, instance.id)
                        await detach_for_instance(session, instance.id)
                        # delete_instance 404 容错,顺带清残留的 Service/Ingress(防孤儿端点占
                        # NodePort);事务外执行,失败由泄漏回收宽限期后强删兜底
                        ns, uuid = instance.k8s_namespace, instance.uuid
                        post_commit.append(
                            (
                                "delete_instance",
                                lambda ns=ns, uuid=uuid: orch.delete_instance(ns, uuid),
                            )
                        )
                        if first_boot:
                            # creating 超时的盘从未承载数据,可回收;starting 超时不删盘
                            # (盘里有上一轮数据)。交 outbox 让 handler 等 Pod 消失再删
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
                            target_id=instance.uuid,
                        )
                    elif ready:
                        # 开了 SSH 却没有端口落库:不推进 running(/access 与重启都依赖 ssh_port,
                        # 补发的端口未必等于 Service 已建的 nodePort),留着等超时转 failed
                        logger.warning("instance_ready_without_port", instance_id=instance.id)

                elif instance.status == sm_def.RUNNING:
                    node_not_ready = (
                        None
                        if not_ready_by_node is None or instance.node_name is None
                        else not_ready_by_node.get(instance.node_name)
                    )
                    lost = await _running_pod_lost_reason(
                        session, instance, st, unready_timeout, node_not_ready
                    )
                    # lost is None 时不动 unready_since:清零只由 _running_pod_lost_reason
                    # 在 st.ready 分支做。在这里按 None 清零 = 与写入同事务擦掉,计时永不累积
                    if lost is not None:
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
                        # 失联节点上的 Pod 只有强删才会从 etcd 消失;事务外执行,
                        # 失败由泄漏回收宽限期后强删兜底
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
                                target_id=instance.uuid,
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
                        await _escalate_stuck(
                            session,
                            instance,
                            age=now_utc() - entered_at[instance_id],
                            stuck_after=stop_timeout,
                            task_type="instance.stop",
                            counts=counts,
                            stuck=stuck,
                            post_commit=post_commit,
                        )

                elif instance.status == sm_def.RELEASING:
                    if not st.exists:
                        await transition(
                            session, instance, sm_def.RELEASED, reason="released", actor="system"
                        )
                        await free_port(session, instance.id)
                        await detach_for_instance(session, instance.id)
                        # 释放是实例盘唯一的销毁时点:统一交 outbox(handler 等 Pod 消失再删,
                        # at-least-once + 死信重派兜底,事务内零 K8s 调用)
                        enqueue(session, "instance.disk_cleanup", {"instance_id": instance.id})
                        counts["to_released"] += 1
                    else:
                        await _escalate_stuck(
                            session,
                            instance,
                            age=now_utc() - entered_at[instance_id],
                            stuck_after=release_timeout,
                            task_type="instance.release",
                            counts=counts,
                            stuck=stuck,
                            post_commit=post_commit,
                        )

                await session.commit()
            # 事务已提交,K8s 清理在锁外执行;失败仅记日志不中断:Pod 残留由泄漏回收兜底,
            # 强删失败下轮同分支重试,盘删除走 outbox 重派
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
    必须豁免带 batch.kubernetes.io/job-name 标签的 Pod:它们是受管 Job(wipe/quota)的
    子孙,DB 必然无记录,误删会让擦盘陷入「建-杀死」循环(Job 泄漏由 TTL 兜底)。
    熔断:未知 Pod 占比超阈即中止本轮并告警,不按陌生对象清单批量强删。
    """
    settings = get_settings()
    orch = get_orchestrator()
    entries = await orch.list_instance_pods()
    # Job 子孙 Pod 既不计入 unknown 分子也不计入分母:否则 wipe Pod 会把占比推向熔断线
    pods = [e for e in entries if JOB_NAME_LABEL not in e.labels]
    by_uuid, entered_at = await _instances_by_object_name(sm, [e.name for e in pods])
    unknown = sum(1 for e in pods if e.name not in by_uuid)
    if pods and _breaker_tripped(unknown, len(pods), label="leak_reclaim_aborted"):
        return
    counts["job_pod_skipped"] += len(entries) - len(pods)

    stop_grace = timedelta(seconds=settings.stopping_timeout_seconds) * 2
    release_grace = timedelta(seconds=settings.releasing_timeout_seconds) * 2
    for e in pods:
        instance = by_uuid.get(e.name)
        db_status = instance.status if instance is not None else None
        # Pod 应该存在的状态:creating/starting/running
        if db_status in (sm_def.CREATING, sm_def.STARTING, sm_def.RUNNING):
            continue
        if instance is not None and db_status in (
            sm_def.STOPPING,
            sm_def.RELEASING,
            sm_def.STOPPED,
            sm_def.FAILED,
        ):
            # 在途删除与 restart 建 Pod 窗口都给宽限,超时由本函数强删(与主对账同阈值)
            grace = release_grace if db_status == sm_def.RELEASING else stop_grace
            if now_utc() - entered_at[instance.id] <= grace:
                continue
        await _reclaim(orch, e.namespace, e.name, "leaked_pod_reclaimed", db_status, counts)

    # 孤儿 Service/Ingress(Pod 已消失但端点残留)会继续占 NodePort,同一熔断比例下清理
    try:
        endpoints = await orch.list_instance_endpoints()
    except Exception:
        logger.exception("reconcile_list_endpoints_failed")
        return
    if not endpoints:
        return
    ep_by_uuid, ep_entered = await _instances_by_object_name(sm, [n for _ns, n in endpoints])
    unknown_ep = sum(1 for _ns, n in endpoints if n not in ep_by_uuid)
    if _breaker_tripped(unknown_ep, len(endpoints), label="endpoint_reclaim_aborted"):
        return
    for ns, name in endpoints:
        inst = ep_by_uuid.get(name)
        if inst is not None and inst.status in ACTIVE_STATUSES:
            continue
        if inst is not None and now_utc() - ep_entered[inst.id] <= stop_grace:
            continue
        db_status = inst.status if inst is not None else None
        await _reclaim(orch, ns, name, "leaked_endpoint_reclaimed", db_status, counts)


async def _instances_by_object_name(
    sm: async_sessionmaker[AsyncSession], names: Iterable[str]
) -> tuple[dict[str, Instance], dict[int, Any]]:
    """K8s 对象名(= 实例 uuid)→ 实例,并为非活跃态实例算「进入状态时刻」(宽限判定用);
    活跃态的对象本就该存在,不需要时刻。"""
    uuids = list(names)
    if not uuids:
        return {}, {}
    async with sm() as session:
        instances = list(
            (await session.execute(select(Instance).where(Instance.uuid.in_(uuids)))).scalars()
        )
        in_flight = [
            i
            for i in instances
            if i.status not in (sm_def.CREATING, sm_def.STARTING, sm_def.RUNNING)
        ]
        entered_at = await _entered_status_map(session, _status_keys(in_flight))
    return {i.uuid: i for i in instances}, entered_at


def _breaker_tripped(unknown: int, total: int, *, label: str) -> bool:
    """未知(DB 无记录)对象占比超阈 → 中止本轮并计指标:LIST 与 DB 大面积不一致
    (接错集群/标签漂移)时不能按陌生清单批量强删。"""
    ratio = get_settings().leak_reclaim_abort_ratio
    if unknown and unknown / total > ratio:
        RECONCILE_LEAK_ABORTED_TOTAL.inc()
        logger.error(label, total=total, unknown=unknown, ratio=ratio)
        return True
    return False


async def _reclaim(
    orch: Any, ns: str, name: str, label: str, db_status: str | None, counts: dict[str, int]
) -> None:
    """强删泄漏对象(404 容错,幂等清 Pod/Service/Ingress)并计数。"""
    logger.error(label, namespace=ns, name=name, db_status=db_status)
    RECONCILE_LEAKED_TOTAL.inc()
    await orch.delete_instance(ns, name, force=True)
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
    只看死信不按 quota_synced=false 补发:后者会把管理端人工 discarded 的死信每轮复活。"""
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
    """长期 stopped / failed 实例的实例盘保留期 GC(停机盘不无限免费占用)。

    stopped:先预警(保留期 - stopped_retention_warn_days)再转 releasing;
    failed:直接到期转 releasing(实例盘从未被收费但也留不住,配额外的泄漏由此收口)。
    两条路径都经 outbox 走正常释放链路(数据盘不受影响)。
    """
    settings = get_settings()
    now = now_utc()
    stop_after = timedelta(days=settings.stopped_retention_days)
    warn_after = stop_after - timedelta(days=settings.stopped_retention_warn_days)
    fail_after = timedelta(days=settings.failed_retention_days)
    # 年龄条件下推到 SQL:进入当前状态不早于创建时刻,created_at 比最小阈值还新的行
    # 不可能到期(超集过滤,不漏不错)
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
        entered_at = await _entered_status_map(session, _status_keys(instances))

    for instance in instances:
        age = now - entered_at[instance.id]
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
                        target_id=fresh.uuid,
                    )
                    await session.commit()
                    counts["gc_released"] += 1
            elif instance.status == sm_def.STOPPED and age > stop_after:
                async with sm() as session:
                    fresh = await session.get(Instance, instance.id)
                    if fresh is None or fresh.status != sm_def.STOPPED:
                        continue
                    await transition(
                        session, fresh, sm_def.RELEASING, reason="retention_reclaim", actor="system"
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
                        target_id=fresh.uuid,
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
                        target_id=instance.uuid,
                    )
                    await session.commit()
                    counts["gc_warned"] += 1
        except Exception:
            logger.exception("gc_retention_failed", instance_id=instance.id)
