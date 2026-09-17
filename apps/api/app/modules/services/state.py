"""Derive the service status from released_at and the current / candidate instances; desired_state
is not read, nothing is stored."""

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
    """Returns (status, ready); ready is true only when running without an observed not-ready."""
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
