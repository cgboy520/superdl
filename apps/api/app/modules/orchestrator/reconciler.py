"""reconciler:每 30s 全量比对「DB 期望 ↔ K8s 实际」并收敛。

- creating/starting + Pod Ready → running;超时未 Ready → failed + 清理
  (服务型且 health_path 非空、容器已实际运行的超时:事件带 occupied_since,按占用时段出账,
  不退包周期预付、不清实例盘)
- running + Pod 消失/异常/持续 not-ready → failed + 告警 + 通知
- stopping + Pod 消失 → stopped;releasing + Pod 消失 → released
- K8s 存在但 DB 已终态的 Pod → 超宽限期强删(未知 Pod 占比超阈即熔断)
- 长期 stopped / failed 的实例盘保留期 GC(先预警,到期 releasing)

每轮一次 list_instance_pods 即状态源,不逐实例 get_status。
stopping/releasing 悬挂两档:一档 outbox 重发删除,二档 force 强删。
"""

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.k8s import K8sOrchestrator, PodStatus, get_orchestrator
from app.core.k8s.base import JOB_NAME_LABEL
from app.core.locks import LockKey, advisory_lock
from app.core.logging import get_logger
from app.core.metrics import (
    DISK_PROVISION_FAILED_TOTAL,
    INSTANCE_NODE_LOST_TOTAL,
    RECONCILE_LEAK_ABORTED_TOTAL,
    RECONCILE_LEAKED_TOTAL,
    RECONCILE_STUCK_INSTANCES,
    SCHEDULE_TIMEOUT_OCCUPIED_TOTAL,
    SSH_PORT_POOL,
)
from app.core.money import money_str
from app.core.outbox import RUNNING_TIMEOUT, OutboxTask, enqueue
from app.core.pricing import MARKET_SUBSCRIPTION
from app.core.timeutil import ensure_utc, now_utc
from app.modules.billing import service as billing_service
from app.modules.notify import service as notify_service
from app.modules.orchestrator import statemachine as sm_def
from app.modules.orchestrator.disks import detach_for_instance
from app.modules.orchestrator.models import DataDisk, Instance, InstanceEvent, PortAllocation
from app.modules.orchestrator.ports import free_port, port_pool_stats
from app.modules.orchestrator.transitions import transition

logger = get_logger(__name__)

ACTIVE_STATUSES = (
    sm_def.CREATING,
    sm_def.STARTING,
    sm_def.RUNNING,
    sm_def.STOPPING,
    sm_def.RELEASING,
)

PostCommit = Callable[[], Awaitable[None]] | None


@dataclass
class _Round:
    """一轮实例对账的共享上下文。"""

    orch: K8sOrchestrator
    settings: Settings
    entered_at: dict[int, datetime]
    not_ready_by_node: dict[str, bool] | None
    counts: dict[str, int]
    stuck: dict[str, int]


async def reconcile_once(sm: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """单轮对账(advisory lock 单实例执行),返回动作计数。"""
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
        "deprovision_redriven": 0,
        "provision_redriven": 0,
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
        await _refresh_port_pool_gauge(sm)
    return counts


async def _refresh_port_pool_gauge(sm: async_sessionmaker[AsyncSession]) -> None:
    """上报端口池指标;容量为配置段长减去段内排除端口数。"""
    settings = get_settings()
    excluded_in_range = sum(
        1
        for p in settings.ssh_port_excluded
        if settings.ssh_port_range_start <= p <= settings.ssh_port_range_end
    )
    capacity = settings.ssh_port_range_end - settings.ssh_port_range_start + 1 - excluded_in_range
    async with sm() as session:
        stats = await port_pool_stats(session)
    SSH_PORT_POOL.labels(state="assigned").set(stats.assigned)
    SSH_PORT_POOL.labels(state="blocked").set(stats.blocked)
    SSH_PORT_POOL.labels(state="free").set(max(0, capacity - stats.assigned - stats.blocked))


def _status_keys(instances: Iterable[Instance]) -> list[tuple[int, str, Any]]:
    """_entered_status_map 的输入形态:(instance_id, 当前状态, created_at)。"""
    return [(i.id, i.status, i.created_at) for i in instances]


async def _entered_status_map(
    session: AsyncSession, keys: Iterable[tuple[int, str, Any]]
) -> dict[int, Any]:
    """批量取「进入当前状态的时刻」:该状态最后一条事件的时刻(不用 updated_at),
    无事件回落 created_at。"""
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
    """running 实例是否已不可用:返回迁移 reason,None = 还活着。
    持续 not-ready 超宽限后分流:节点失联(或节点视图不可用)→ node_lost,节点正常 → pod_unready;
    服务型实例不判 pod_unready。unready_since 只在 st.ready 分支清零,调用方不得按 None 清零。
    """
    if not st.exists or st.phase in ("Failed", "Succeeded"):
        return "pod_lost"
    if st.deleting:
        return "pod_lost"
    if st.ready:
        if instance.unready_since is not None:
            instance.unready_since = None
            await session.flush()
        return None
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
    """悬挂恢复第一档:重发删除任务;已有在途同型任务(pending,或 running 且租约未超
    RUNNING_TIMEOUT)则跳过。"""
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


_MISSING_POD = PodStatus(exists=False)


def _statuses_from_listing(
    rows: list[tuple[int, str, str, str, Any]], listing: list[PodStatus]
) -> list[PodStatus]:
    """全量 LIST 即状态源,与 get_status 同口径(ready/phase/node_name/deleting)。"""
    by_key = {(e.namespace, e.name): e for e in listing}
    return [by_key.get((ns, uuid), _MISSING_POD) for _id, _status, ns, uuid, _created in rows]


async def _node_readiness() -> dict[str, bool] | None:
    """节点 → 是否 NotReady;节点视图不可用返回 None。"""
    try:
        nodes = await get_orchestrator().list_nodes()
    except Exception:
        logger.exception("reconcile_list_nodes_failed")
        return None
    return {n.name: n.status != "Ready" for n in nodes}


async def _reconcile_instances(
    sm: async_sessionmaker[AsyncSession], counts: dict[str, int]
) -> None:
    """活跃态实例逐台收敛:每实例独立事务,单个失败不拖垮整轮;K8s 清理动作在 commit 后执行。"""
    orch = get_orchestrator()
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
        entered_at = await _entered_status_map(
            session, [(iid, status, created) for iid, status, _ns, _uuid, created in rows]
        )
    if not rows:
        RECONCILE_STUCK_INSTANCES.labels(status=sm_def.STOPPING).set(0)
        RECONCILE_STUCK_INSTANCES.labels(status=sm_def.RELEASING).set(0)
        return
    try:
        listing = await orch.list_instance_pods()
    except Exception:
        logger.exception("reconcile_list_pods_failed")
        return
    statuses = _statuses_from_listing(rows, listing)
    not_ready_by_node = await _node_readiness()
    ctx = _Round(
        orch=orch,
        settings=get_settings(),
        entered_at=entered_at,
        not_ready_by_node=not_ready_by_node,
        counts=counts,
        stuck={sm_def.STOPPING: 0, sm_def.RELEASING: 0},
    )

    for (instance_id, row_status, _ns, _uuid, _created), st in zip(rows, statuses, strict=True):
        try:
            post_commit: PostCommit = None
            async with sm() as session:
                instance = await session.get(Instance, instance_id)
                if instance is None or instance.status != row_status:
                    continue
                post_commit = await _reconcile_one(session, ctx, instance, st)
                await session.commit()
            if post_commit is not None:
                try:
                    await post_commit()
                except Exception:
                    logger.exception("reconcile_k8s_cleanup_failed", instance_id=instance_id)
        except Exception:
            logger.exception("reconcile_instance_failed", instance_id=instance_id)

    RECONCILE_STUCK_INSTANCES.labels(status=sm_def.STOPPING).set(ctx.stuck[sm_def.STOPPING])
    RECONCILE_STUCK_INSTANCES.labels(status=sm_def.RELEASING).set(ctx.stuck[sm_def.RELEASING])


async def _reconcile_one(
    session: AsyncSession, ctx: _Round, instance: Instance, st: PodStatus
) -> PostCommit:
    """单台实例按状态派发;事务内只做状态迁移 / 标记 / enqueue,返回 commit 后要跑的 K8s 动作。"""
    match instance.status:
        case sm_def.CREATING | sm_def.STARTING:
            return await _reconcile_booting(session, ctx, instance, st)
        case sm_def.RUNNING:
            return await _reconcile_running(session, ctx, instance, st)
        case sm_def.STOPPING:
            return await _reconcile_terminating(
                session,
                ctx,
                instance,
                st,
                target=sm_def.STOPPED,
                reason="pod_deleted",
                counter="to_stopped",
                stuck_after=timedelta(seconds=ctx.settings.stopping_timeout_seconds),
                task_type="instance.stop",
            )
        case sm_def.RELEASING:
            return await _reconcile_terminating(
                session,
                ctx,
                instance,
                st,
                target=sm_def.RELEASED,
                reason="released",
                counter="to_released",
                stuck_after=timedelta(seconds=ctx.settings.releasing_timeout_seconds),
                task_type="instance.release",
            )
    return None


async def _reconcile_booting(
    session: AsyncSession, ctx: _Round, instance: Instance, st: PodStatus
) -> PostCommit:
    """creating / starting:Pod Ready(且 SSH 端口已落库)→ running;超时 → failed + 清理。"""
    ready = st.exists and st.ready
    port_ok = instance.ssh_port is not None or not instance.with_ssh
    if ready and port_ok:
        instance.node_name = st.node_name
        await transition(session, instance, sm_def.RUNNING, reason="pod_ready", actor="system")
        ctx.counts["to_running"] += 1
        return None
    timeout = timedelta(seconds=ctx.settings.creating_timeout_seconds)
    if now_utc() - ctx.entered_at[instance.id] <= timeout:
        if ready:
            logger.warning("instance_ready_without_port", instance_id=instance.id)
        return None
    first_boot = instance.status == sm_def.CREATING
    occupied_since = _tenant_occupied_since(instance, st)
    meta = {"occupied_since": occupied_since.isoformat()} if occupied_since else None
    post_commit = await _fail_instance(
        session, ctx, instance, reason="schedule_timeout", metadata=meta
    )
    platform_fault_first_boot = first_boot and occupied_since is None
    refunded: Decimal | None = None
    if platform_fault_first_boot and instance.market == MARKET_SUBSCRIPTION:
        refunded = await billing_service.refund_unstarted_subscription(
            session, instance.id, instance.user_id
        )
    if platform_fault_first_boot:
        enqueue(session, "instance.disk_cleanup", {"instance_id": instance.id})
    if occupied_since is not None:
        SCHEDULE_TIMEOUT_OCCUPIED_TOTAL.inc()
        logger.warning(
            "instance_schedule_timeout_occupied",
            instance_id=instance.id,
            occupied_seconds=int((now_utc() - occupied_since).total_seconds()),
        )
    else:
        logger.warning("instance_schedule_timeout", instance_id=instance.id)
    await _notify_schedule_timeout(
        session, instance, refunded=refunded, tenant_fault=occupied_since is not None
    )
    return post_commit


def _tenant_occupied_since(instance: Instance, st: PodStatus) -> datetime | None:
    """就绪由租户控制(服务型 + health_path)且 workspace 容器已实际运行 → 占用起点;
    其余超时按平台责任返回 None。"""
    if instance.workload_type != "service" or not instance.health_path:
        return None
    if not st.exists or st.started_at is None:
        return None
    return ensure_utc(st.started_at)


async def _notify_schedule_timeout(
    session: AsyncSession, instance: Instance, *, refunded: Decimal | None, tenant_fault: bool
) -> None:
    if tenant_fault:
        title = "服务启动失败:健康检查超时"
        content = (
            f"服务实例「{instance.name}」在超时内未通过 health_path 健康检查,已自动终止;"
            "容器实际运行时段已按量计费,包周期预付不退。修复健康检查后可重新启动。"
        )
    else:
        title = "实例创建失败:调度超时"
        content = (
            f"实例「{instance.name}」调度或镜像拉取超时,已自动终止,"
            + (
                f"包周期预付 {money_str(refunded)} 元已原额退回余额。"
                if refunded
                else "未产生任何费用。"
            )
            + "可换个档位重试,或稍后再试;多次失败请联系客服。"
        )
    await notify_service.notify(
        session,
        instance.user_id,
        type_="instance",
        title=title,
        content=content,
        severity="warning",
        dedup_key=f"schedule_timeout:{instance.id}",
        target_id=instance.uuid,
    )


async def _reconcile_running(
    session: AsyncSession, ctx: _Round, instance: Instance, st: PodStatus
) -> PostCommit:
    """running:Pod 消失 / 异常 / 持续 not-ready 超宽限 → failed(node_lost 强删并通知)。"""
    node_not_ready = (
        None
        if ctx.not_ready_by_node is None or instance.node_name is None
        else ctx.not_ready_by_node.get(instance.node_name)
    )
    unready_timeout = timedelta(seconds=ctx.settings.running_unready_timeout_seconds)
    lost = await _running_pod_lost_reason(session, instance, st, unready_timeout, node_not_ready)
    if lost is None:
        return None
    meta: dict[str, Any] = {"phase": st.phase if st.exists else "Missing", "ready": st.ready}
    if lost in ("node_lost", "pod_lost") and instance.unready_since is not None:
        meta["unready_since"] = ensure_utc(instance.unready_since).isoformat()
    post_commit = await _fail_instance(
        session, ctx, instance, reason=lost, metadata=meta, force_delete=lost == "node_lost"
    )
    if lost != "node_lost":
        logger.error("instance_pod_lost", instance_id=instance.id, reason=lost)
        return post_commit
    INSTANCE_NODE_LOST_TOTAL.inc()
    logger.error("instance_node_lost", instance_id=instance.id, node=instance.node_name)
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
    return post_commit


async def _fail_instance(
    session: AsyncSession,
    ctx: _Round,
    instance: Instance,
    *,
    reason: str,
    metadata: dict[str, Any] | None = None,
    force_delete: bool = False,
) -> PostCommit:
    """→ failed 的公共收尾:迁移、放端口、解挂数据盘、计数;返回事务外删 Pod/Service/HTTPRoute 的动作
    (404 容错;失败由泄漏回收兜底)。"""
    await transition(
        session, instance, sm_def.FAILED, reason=reason, actor="system", metadata=metadata
    )
    await free_port(session, instance.id)
    await detach_for_instance(session, instance.id)
    ctx.counts["to_failed"] += 1
    orch, ns, uuid = ctx.orch, instance.k8s_namespace, instance.uuid

    async def delete_pod() -> None:
        await orch.delete_instance(ns, uuid, force=force_delete)

    return delete_pod


async def _reconcile_terminating(
    session: AsyncSession,
    ctx: _Round,
    instance: Instance,
    st: PodStatus,
    *,
    target: str,
    reason: str,
    counter: str,
    stuck_after: timedelta,
    task_type: str,
) -> PostCommit:
    """stopping / releasing:Pod 消失即到终点;仍在则按悬挂时长两档升级。"""
    if st.exists:
        return await _escalate_stuck(
            session,
            ctx,
            instance,
            age=now_utc() - ctx.entered_at[instance.id],
            stuck_after=stuck_after,
            task_type=task_type,
        )
    await transition(session, instance, target, reason=reason, actor="system")
    if target == sm_def.RELEASED:
        await free_port(session, instance.id)
        await detach_for_instance(session, instance.id)
        enqueue(session, "instance.disk_cleanup", {"instance_id": instance.id})
    ctx.counts[counter] += 1
    return None


async def _escalate_stuck(
    session: AsyncSession,
    ctx: _Round,
    instance: Instance,
    *,
    age: timedelta,
    stuck_after: timedelta,
    task_type: str,
) -> PostCommit:
    """stopping/releasing 悬挂两档:> stuck_after 经 outbox 重发删除;
    > 2×stuck_after 返回事务外 force 强删动作。"""
    status = instance.status
    age_s = int(age.total_seconds())
    if age > stuck_after * 2:
        orch, ns, uuid, iid = ctx.orch, instance.k8s_namespace, instance.uuid, instance.id

        async def force_delete() -> None:
            await orch.delete_instance(ns, uuid, force=True)
            ctx.counts["force_deleted"] += 1
            logger.error(f"{status}_force_deleted", instance_id=iid, age_seconds=age_s)

        return force_delete
    if age > stuck_after:
        ctx.stuck[status] += 1
        if await _reenqueue_delete(session, task_type, instance.id):
            ctx.counts["delete_requeued"] += 1
        logger.warning(f"{status}_stuck_requeued", instance_id=instance.id, age_seconds=age_s)
    return None


async def _reclaim_leaked_pods(
    sm: async_sessionmaker[AsyncSession], counts: dict[str, int]
) -> None:
    """K8s 存在但 DB 已终态/无记录的 Pod → 宽限期后 force 强删。
    豁免带 batch.kubernetes.io/job-name 标签的 Pod(受管 Job 子孙);未知 Pod 占比超阈即熔断。
    """
    settings = get_settings()
    orch = get_orchestrator()
    entries = await orch.list_instance_pods()
    pods = [e for e in entries if JOB_NAME_LABEL not in e.labels]
    by_uuid, entered_at = await _instances_by_object_name(sm, [e.name for e in pods])
    unknown = sum(1 for e in pods if e.name not in by_uuid)
    if pods and _breaker_tripped(unknown, len(pods), label="leak_reclaim_aborted"):
        return
    counts["job_pod_skipped"] += len(entries) - len(pods)

    stop_grace = timedelta(seconds=settings.stopping_timeout_seconds) * 2
    release_grace = timedelta(seconds=settings.releasing_timeout_seconds) * 2
    now = now_utc()
    for e in pods:
        instance = by_uuid.get(e.name)
        if instance is None:
            await _reclaim(orch, e.namespace, e.name, "leaked_pod_reclaimed", None, counts)
            continue
        if instance.status in (sm_def.CREATING, sm_def.STARTING, sm_def.RUNNING):
            continue
        grace = release_grace if instance.status == sm_def.RELEASING else stop_grace
        if now - entered_at[instance.id] <= grace:
            continue
        await _reclaim(orch, e.namespace, e.name, "leaked_pod_reclaimed", instance.status, counts)

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
        if inst is None:
            await _reclaim(orch, ns, name, "leaked_endpoint_reclaimed", None, counts)
            continue
        if inst.status in ACTIVE_STATUSES or now - ep_entered[inst.id] <= stop_grace:
            continue
        await _reclaim(orch, ns, name, "leaked_endpoint_reclaimed", inst.status, counts)


async def _instances_by_object_name(
    sm: async_sessionmaker[AsyncSession], names: Iterable[str]
) -> tuple[dict[str, Instance], dict[int, Any]]:
    """K8s 对象名(= 实例 uuid)→ 实例,并为非活跃态实例算「进入状态时刻」。"""
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
    """未知(DB 无记录)对象占比超阈 → 中止本轮并计指标。"""
    ratio = get_settings().leak_reclaim_abort_ratio
    if unknown and unknown / total > ratio:
        RECONCILE_LEAK_ABORTED_TOTAL.inc()
        logger.error(label, total=total, unknown=unknown, ratio=ratio)
        return True
    return False


async def _reclaim(
    orch: K8sOrchestrator,
    ns: str,
    name: str,
    label: str,
    db_status: str | None,
    counts: dict[str, int],
) -> None:
    """强删泄漏对象(404 容错,清 Pod/Service/Ingress)并计数。"""
    logger.error(label, namespace=ns, name=name, db_status=db_status)
    RECONCILE_LEAKED_TOTAL.inc()
    await orch.delete_instance(ns, name, force=True)
    counts["leaked"] += 1


async def _recheck_blocked_ports(
    sm: async_sessionmaker[AsyncSession], counts: dict[str, int]
) -> None:
    """blocked 端口周期复检:集群侧占用已消失即放回池。"""
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


async def _redrive_dead_disk_tasks(
    sm: async_sessionmaker[AsyncSession],
    *,
    task_type: str,
    cooldown: timedelta,
    should_redrive: Callable[[DataDisk], bool],
    log_event: str,
) -> int:
    """数据盘类 outbox 死信重派:死信行保留,盘仍需要该动作且无在途同盘任务时补发新任务。
    cooldown = 死信静置多久才重派(0 = 立即)。返回重派数。"""
    async with sm() as session:
        in_flight_ids = {
            int(r[0])
            for r in (
                await session.execute(
                    select(OutboxTask.payload["disk_id"].as_string()).where(
                        OutboxTask.type == task_type,
                        OutboxTask.status.in_(("pending", "running")),
                    )
                )
            ).all()
            if r[0] and r[0].isdigit()
        }
        dead = list(
            (
                await session.execute(
                    select(OutboxTask).where(
                        OutboxTask.type == task_type,
                        OutboxTask.status == "dead",
                        OutboxTask.updated_at < now_utc() - cooldown,
                    )
                )
            ).scalars()
        )
        redriven = 0
        for task in dead:
            disk_id = int(task.payload["disk_id"])
            disk = await session.get(DataDisk, disk_id)
            if disk is None or disk_id in in_flight_ids or not should_redrive(disk):
                continue
            enqueue(session, task_type, {"disk_id": disk_id})
            in_flight_ids.add(disk_id)
            redriven += 1
        if redriven:
            await session.commit()
            logger.warning(log_event, count=redriven)
    return redriven


async def _redrive_dead_disk_wipes(
    sm: async_sessionmaker[AsyncSession], counts: dict[str, int]
) -> None:
    """disk.deprovision 死信立即重派(盘仍在 deleting)。"""
    counts["deprovision_redriven"] = await _redrive_dead_disk_tasks(
        sm,
        task_type="disk.deprovision",
        cooldown=timedelta(0),
        should_redrive=lambda disk: disk.status == "deleting",
        log_event="disk_deprovision_redriven",
    )


async def _reconcile_disk_quotas(
    sm: async_sessionmaker[AsyncSession], counts: dict[str, int]
) -> None:
    """disk.provision 死信超 1 小时重派并计指标;只看死信,不按 provisioned=false 补发。"""
    redriven = await _redrive_dead_disk_tasks(
        sm,
        task_type="disk.provision",
        cooldown=timedelta(hours=1),
        should_redrive=lambda disk: (
            disk.status not in ("deleting", "deleted") and not disk.provisioned
        ),
        log_event="disk_provision_redriven",
    )
    DISK_PROVISION_FAILED_TOTAL.inc(redriven)
    counts["provision_redriven"] = redriven


async def _gc_retention(sm: async_sessionmaker[AsyncSession], counts: dict[str, int]) -> None:
    """长期 stopped / failed 实例盘保留期 GC:stopped 先预警(保留期 - warn_days)再转 releasing,
    failed 到期直接转 releasing;都经 outbox 走正常释放链路,数据盘不受影响。
    """
    settings = get_settings()
    now = now_utc()
    stop_after = timedelta(days=settings.stopped_retention_days)
    warn_after = stop_after - timedelta(days=settings.stopped_retention_warn_days)
    fail_after = timedelta(days=settings.failed_retention_days)
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
                released = await _gc_release(
                    sm,
                    instance,
                    reason="failed_retention_reclaim",
                    title="失败实例已自动释放",
                    content=(
                        f"实例「{instance.name}」启动失败后超过 "
                        f"{settings.failed_retention_days} 天未处理,已自动释放"
                        "(实例盘清除,数据盘不受影响)。"
                    ),
                    dedup_prefix="failed_retention",
                )
                counts["gc_released"] += released
            elif instance.status == sm_def.STOPPED and age > stop_after:
                released = await _gc_release(
                    sm,
                    instance,
                    reason="retention_reclaim",
                    title="停机实例已自动释放",
                    content=(
                        f"实例「{instance.name}」已停机超过 "
                        f"{settings.stopped_retention_days} 天,按保留期策略自动释放"
                        "(实例盘清除,数据盘不受影响)。"
                    ),
                    dedup_prefix="retention_reclaim",
                )
                counts["gc_released"] += released
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


async def _gc_release(
    sm: async_sessionmaker[AsyncSession],
    instance: Instance,
    *,
    reason: str,
    title: str,
    content: str,
    dedup_prefix: str,
) -> int:
    """保留期到期释放:重读复核状态未变 → releasing + outbox + 站内信,同事务提交。返回 0/1。"""
    async with sm() as session:
        fresh = await session.get(Instance, instance.id)
        if fresh is None or fresh.status != instance.status:
            return 0
        await transition(session, fresh, sm_def.RELEASING, reason=reason, actor="system")
        enqueue(session, "instance.release", {"instance_id": fresh.id})
        await notify_service.notify(
            session,
            fresh.user_id,
            type_="instance",
            title=title,
            content=content,
            severity="warning",
            dedup_key=f"{dedup_prefix}:{fresh.id}",
            target_id=fresh.uuid,
        )
        await session.commit()
    return 1
