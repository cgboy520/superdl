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

# RELEASED 是唯一终态;FAILED 为故障停机态,仍可走 releasing→released
TERMINAL = frozenset({RELEASED})

# from → 允许的 to。RUNNING→FAILED 仅系统使用(pod_lost);
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

# 计费态:处于该状态即产生 GPU 时费
BILLABLE = frozenset({RUNNING})


def validate_transition(from_status: str, to_status: str) -> None:
    allowed = TRANSITIONS.get(from_status, frozenset())
    if to_status not in allowed:
        raise AppError(
            ErrorCode.INSTANCE_INVALID_TRANSITION,
            f"实例当前状态({from_status})不允许该操作",
            detail={"from": from_status, "to": to_status},
        )
