from functools import lru_cache

from app.core.config import get_settings
from app.core.k8s.base import InstancePodSpec, K8sOrchestrator, NodePortTaken, PodStatus
from app.core.k8s.fake import FakeOrchestrator

__all__ = [
    "FakeOrchestrator",
    "InstancePodSpec",
    "K8sOrchestrator",
    "NodePortTaken",
    "PodStatus",
    "get_orchestrator",
    "set_orchestrator",
]

_override: K8sOrchestrator | None = None


@lru_cache
def _default_orchestrator() -> K8sOrchestrator:
    settings = get_settings()
    if settings.k8s_backend == "real":  # pragma: no cover - 需要真实集群
        from app.core.k8s.real import RealOrchestrator

        return RealOrchestrator()
    return FakeOrchestrator()


def get_orchestrator() -> K8sOrchestrator:
    return _override if _override is not None else _default_orchestrator()


def set_orchestrator(orch: K8sOrchestrator | None) -> None:
    """测试注入。传 None 恢复默认。"""
    global _override
    _override = orch
