"""按 SUPERDL_WORKER_COMPONENT 划分 outbox 任务;定时任务归属见 workers/jobs.py。

每种 outbox 任务必须归属一个组件;ALL 处理全部组件的任务。
"""

from enum import StrEnum

from app.core.config import get_settings


class WorkerComponent(StrEnum):
    ALL = "all"
    CORE = "core"
    TENANT_MGR = "tenant-mgr"
    NODE_MGR = "node-mgr"
    PREWARM = "prewarm"
    DISK_OPS = "disk-ops"


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
    WorkerComponent.NODE_MGR: frozenset({"node.cordon", "node.decommission", "node.switch_pool"}),
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
