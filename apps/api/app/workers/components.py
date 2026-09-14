"""worker 组件划分:outbox 任务类型与定时任务按组件分片,每个 Deployment 经 SUPERDL_WORKER_COMPONENT
声明身份(K8s 权限随组件收窄,见 deploy/app/k8s/01-rbac.yaml);ALL 为 dev/test 单进程全量。
新增 outbox handler 必须登记到某个组件(tests/test_workers_components.py 锚定);
定时任务的归属在 workers/jobs.py 的 JOBS 清单里声明。
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


# outbox 任务类型 → 组件
COMPONENT_OUTBOX_TYPES: dict[WorkerComponent, frozenset[str]] = {
    WorkerComponent.CORE: frozenset({"notify.sms"}),
    WorkerComponent.TENANT_MGR: frozenset(
        {
            "instance.create",
            "instance.start",
            "instance.stop",
            "instance.restart",
            "instance.release",
            "instance.disk_cleanup",
            "service.retire",
        }
    ),
    WorkerComponent.NODE_MGR: frozenset({"node.cordon", "node.decommission"}),
    WorkerComponent.PREWARM: frozenset({"image.prewarm"}),
    WorkerComponent.DISK_OPS: frozenset({"disk.provision", "disk.deprovision"}),
}


def current_component() -> WorkerComponent:
    """SUPERDL_WORKER_COMPONENT 解析;缺省 ALL,非法值拒启。"""
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
