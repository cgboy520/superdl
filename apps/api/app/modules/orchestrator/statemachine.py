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

# RELEASED 是唯一的最终终态;FAILED 是「故障停机」态,用户释放后仍走 releasing→released,
# 否则失败实例永远留在列表里删不掉(状态机无出边 = 死局)。
TERMINAL = frozenset({RELEASED})

# (from → 允许的 to)。RUNNING→FAILED 仅系统使用(pod_lost:节点/Pod 故障)
# CREATING→RELEASING:用户主动取消(调度长期不满足时不必干等超时);creating 未进入
# 计费态,取消不产生 GPU 时费,冻结额度随 release 退回。
# FAILED→RELEASING:用户清理失败实例(资源已停,释放只做残留清理与列表出清)。
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
