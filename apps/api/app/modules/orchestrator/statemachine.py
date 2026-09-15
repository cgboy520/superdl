"""实例状态机:running↔非 running 的边即计费边;状态变更只经 service.transition()。"""

from app.core.errors import AppError, ErrorCode

CREATING = "creating"
RUNNING = "running"
STOPPING = "stopping"
STOPPED = "stopped"
STARTING = "starting"
FROZEN = "frozen"
RELEASING = "releasing"
RELEASED = "released"
FAILED = "failed"

TRANSITIONS: dict[str, frozenset[str]] = {
    CREATING: frozenset({RUNNING, FAILED, RELEASING}),
    RUNNING: frozenset({STOPPING, FAILED}),
    STOPPING: frozenset({STOPPED, RELEASING}),
    STOPPED: frozenset({STARTING, FROZEN, RELEASING}),
    STARTING: frozenset({RUNNING, FAILED}),
    FROZEN: frozenset({STOPPED, RELEASING}),
    FAILED: frozenset({STOPPED, RELEASING}),
    RELEASING: frozenset({RELEASED}),
}


def validate_transition(from_status: str, to_status: str) -> None:
    allowed = TRANSITIONS.get(from_status, frozenset())
    if to_status not in allowed:
        raise AppError(
            ErrorCode.INSTANCE_INVALID_TRANSITION,
            key="orchestrator.invalidTransition",
            params={"from": from_status},
            detail={"from": from_status, "to": to_status},
        )
