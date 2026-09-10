"""服务状态派生:纯函数、不落库。单一事实源仍是实例的 status 与 instance_events,
这里只把「desired_state + 当前 / 候选实例」翻译成用户看到的一个词。"""

from typing import TYPE_CHECKING

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

SERVICE_STATUSES: tuple[str, ...] = (
    DEPLOYING,
    RUNNING,
    UNREADY,
    STOPPING,
    STOPPED,
    FROZEN,
    FAILED,
    RELEASING,
    RELEASED,
)

# 实例状态 → 服务状态(与 orchestrator/statemachine 的字面量严格一致;creating/starting 归并为部署中)
_BY_INSTANCE_STATUS: dict[str, str] = {
    "creating": DEPLOYING,
    "starting": DEPLOYING,
    "stopping": STOPPING,
    "stopped": STOPPED,
    "frozen": FROZEN,
    "failed": FAILED,
    "releasing": RELEASING,
    "released": RELEASED,
}


def derive_status(
    service: "Service", current: "Instance | None", rollout: "Instance | None"
) -> tuple[str, bool]:
    """返回 (status, ready)。ready 只在 running 且巡检未观察到 not-ready 时为真:
    服务型实例持续 not-ready 不判故障,unready 是 warning 不是 error。"""
    if service.released_at is not None:
        return RELEASED, False
    if rollout is not None:
        return DEPLOYING, False
    if current is None:
        # 迁移 / 异常兜底:没有实例可指的服务按已停止呈现,不伪装成运行中
        return STOPPED, False
    if current.status == "running":
        ready = current.unready_since is None
        return (RUNNING if ready else UNREADY), ready
    return _BY_INSTANCE_STATUS.get(current.status, current.status), False
