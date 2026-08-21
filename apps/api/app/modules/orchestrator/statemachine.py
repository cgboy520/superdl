"""实例状态机(development-plan §5.1)。

running↔非 running 的边就是计费边。任何状态变更必须经 service.transition()
(同事务写 instance_events),禁止直接 UPDATE status。
"""

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

# from → 允许的 to。RELEASED 是唯一终态(无出边);RUNNING→FAILED 仅系统使用(pod_lost);
# CREATING→RELEASING 为用户取消(creating 非计费态,冻结额度随 release 退回);
# FAILED→RELEASING 为用户清理失败实例。
TRANSITIONS: dict[str, frozenset[str]] = {
    CREATING: frozenset({RUNNING, FAILED, RELEASING}),
    RUNNING: frozenset({STOPPING, FAILED}),
    STOPPING: frozenset({STOPPED}),
    STOPPED: frozenset({STARTING, FROZEN, RELEASING}),
    STARTING: frozenset({RUNNING, FAILED}),
    FROZEN: frozenset({STOPPED, RELEASING}),
    FAILED: frozenset({RELEASING}),
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
