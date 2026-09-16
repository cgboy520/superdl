"""Split outbox tasks by SUPERDL_WORKER_COMPONENT; scheduled-job ownership is in workers/jobs.py.

Every outbox task type must belong to one component; ALL handles every component's tasks.
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
    """Parse SUPERDL_WORKER_COMPONENT; default ALL, an invalid value refuses to start."""
    raw = get_settings().worker_component.strip() or "all"
    try:
        return WorkerComponent(raw)
    except ValueError:
        options = sorted(c.value for c in WorkerComponent)
        raise RuntimeError(
            f"unknown SUPERDL_WORKER_COMPONENT: {raw!r} (options: {options})"
        ) from None


def outbox_types_for(component: WorkerComponent) -> frozenset[str] | None:
    """The component's outbox claim set; ALL returns None (no filter)."""
    if component is WorkerComponent.ALL:
        return None
    return COMPONENT_OUTBOX_TYPES[component]
