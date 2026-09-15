"""从 released_at、当前与候选实例派生服务状态;不读取 desired_state,不落库。"""

from typing import TYPE_CHECKING

from app.modules.orchestrator import statemachine as sm_def

if TYPE_CHECKING:
    from app.modules.orchestrator.models import Instance
    from app.modules.services.models import Service


DEPLOYING = "deploying"
RUNNING = "running"
UNREADY = "unready"
STOPPING = "stopping"
STOPPED = "stopped"
FROZEN = "frozen"
FAILED = "failed"
RELEASING = "releasing"
RELEASED = "released"

_BY_INSTANCE_STATUS: dict[str, str] = {
    sm_def.CREATING: DEPLOYING,
    sm_def.STARTING: DEPLOYING,
    sm_def.STOPPING: STOPPING,
    sm_def.STOPPED: STOPPED,
    sm_def.FROZEN: FROZEN,
    sm_def.FAILED: FAILED,
    sm_def.RELEASING: RELEASING,
    sm_def.RELEASED: RELEASED,
}


def derive_status(
    service: "Service", current: "Instance | None", rollout: "Instance | None"
) -> tuple[str, bool]:
    """返回 (status, ready);ready 只在 running 且未观察到 not-ready 时为真。"""
    if service.released_at is not None:
        return RELEASED, False
    if rollout is not None:
        return DEPLOYING, False
    if current is None:
        return STOPPED, False
    if current.status == "running":
        ready = current.unready_since is None
        return (RUNNING if ready else UNREADY), ready
    return _BY_INSTANCE_STATUS.get(current.status, current.status), False
