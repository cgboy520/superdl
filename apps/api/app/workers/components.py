"""worker 组件划分:单 Pod 单 SA 的前提是 outbox 按任务组件拆 Deployment。

outbox 任务类型与定时任务按组件分片:每个 Deployment 经 SUPERDL_WORKER_COMPONENT
声明身份,只领/只跑本组件的活,K8s 写权限随之按组件收窄(01-rbac.yaml 的
superdl-tenant-mgr / -node-mgr / -prewarm / -disk-ops)。
ALL 是 dev/test 单进程的全量模式,生产各 Deployment 必须显式声明组件。

分片的完备性由 tests/test_workers_components.py 锚定:新增 outbox handler /
定时任务必须登记到某个组件,否则测试红(未登记的任务在生产会静默停摆)。
"""

from enum import StrEnum

from app.core.config import get_settings


class WorkerComponent(StrEnum):
    ALL = "all"
    CORE = "core"  # 计费/通知/保洁:纯 DB + 外部渠道,零 K8s 权限(SA 无 ClusterRoleBinding)
    TENANT_MGR = "tenant-mgr"  # 租户编排:instance.* 生命周期 + reconciler
    NODE_MGR = "node-mgr"  # 节点:cordon/标签收敛/注册 reconciliation
    PREWARM = "prewarm"  # 镜像预热 Job(superdl ns)
    DISK_OPS = "disk-ops"  # 数据盘配额下发/擦除 Job(superdl + tenant ns)


# outbox 任务类型 → 组件。handler 归属模块不改,映射集中在这里
COMPONENT_OUTBOX_TYPES: dict[WorkerComponent, frozenset[str]] = {
    WorkerComponent.CORE: frozenset({"notify.sms"}),
    WorkerComponent.TENANT_MGR: frozenset(
        {
            "instance.create",
            "instance.start",
            "instance.stop",
            "instance.restart",
            "instance.release",
            # Pod 消失确认 + 删实例盘 PVC,是 instance 生命周期的收尾段,同属租户编排面
            "instance.disk_cleanup",
            # 服务版本更新收尾:释放旧版本实例,同属租户编排面
            "service.retire",
        }
    ),
    WorkerComponent.NODE_MGR: frozenset({"node.cordon", "node.decommission"}),
    WorkerComponent.PREWARM: frozenset({"image.prewarm"}),
    WorkerComponent.DISK_OPS: frozenset({"disk.quota", "disk.wipe"}),
}

# 定时任务 id → 组件(与 workers/main.register_scheduled_jobs 的注册清单由
# tests/test_workers_components.py 双向锁定)
COMPONENT_SCHEDULED_JOBS: dict[WorkerComponent, frozenset[str]] = {
    WorkerComponent.CORE: frozenset(
        {
            "outbox_reaper",
            "outbox_metrics",
            "hourly_settlement",
            "daily_disk_settlement",
            "fund_reconcile",
            "usage_aggregation",
            "close_expired_orders",
            "payment_reconcile",
            "cleanup_expired_rows",
            "balance_patrol",
            "subscription_patrol",
            "ticket_stale_patrol",
        }
    ),
    WorkerComponent.TENANT_MGR: frozenset({"reconciler"}),
    WorkerComponent.NODE_MGR: frozenset({"node_spec_patrol", "node_enroll_reconciler"}),
    WorkerComponent.PREWARM: frozenset({"prewarm_patrol"}),
    WorkerComponent.DISK_OPS: frozenset(),
}


def current_component() -> WorkerComponent:
    """SUPERDL_WORKER_COMPONENT 解析。缺省 ALL(dev/test);非法值 fail-closed:
    起错组件 = 该组件队列静默停摆,立即报错远比带病运行安全。"""
    raw = get_settings().worker_component.strip() or "all"
    try:
        return WorkerComponent(raw)
    except ValueError:
        options = sorted(c.value for c in WorkerComponent)
        raise RuntimeError(f"未知 SUPERDL_WORKER_COMPONENT: {raw!r}(可选: {options})") from None


def outbox_types_for(component: WorkerComponent) -> frozenset[str] | None:
    """组件的 outbox 领取集合;ALL 返回 None(不过滤)。"""
    if component is WorkerComponent.ALL:
        return None
    return COMPONENT_OUTBOX_TYPES[component]


def scheduled_jobs_for(component: WorkerComponent) -> frozenset[str] | None:
    """组件的定时任务集合;ALL 返回 None(全注册)。"""
    if component is WorkerComponent.ALL:
        return None
    return COMPONENT_SCHEDULED_JOBS[component]
