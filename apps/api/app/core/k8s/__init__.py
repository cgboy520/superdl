from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.k8s.base import (
    InstancePodSpec,
    K8sOrchestrator,
    NodePortTaken,
    PodStatus,
)
from app.core.k8s.fake import FakeOrchestrator
from app.core.platform_config import get_runtime_config
from app.core.registry import PULL_SECRET_NAME, dockerconfigjson, pull_secret_fingerprint

__all__ = [
    "FakeOrchestrator",
    "InstancePodSpec",
    "K8sOrchestrator",
    "NodePortTaken",
    "PodStatus",
    "ensure_registry_pull_secret",
    "get_orchestrator",
    "set_orchestrator",
]

_override: K8sOrchestrator | None = None


@lru_cache
def _default_orchestrator() -> K8sOrchestrator:
    settings = get_settings()
    if settings.k8s_backend == "real":  # pragma: no cover - 需要真实集群
        # kubernetes 客户端只在 real 后端加载
        from app.core.k8s.real import RealOrchestrator  # noqa: PLC0415

        return RealOrchestrator()
    return FakeOrchestrator()


def get_orchestrator() -> K8sOrchestrator:
    return _override if _override is not None else _default_orchestrator()


def set_orchestrator(orch: K8sOrchestrator | None) -> None:
    """测试注入。传 None 恢复默认。"""
    global _override
    _override = orch


async def ensure_registry_pull_secret(session: AsyncSession, namespace: str) -> str | None:
    """在 namespace 托管拉取凭据 Secret,返回 Secret 名;未配机器人返回 None。只在 worker 侧调用。"""
    cfg = await get_runtime_config(session)
    host, robot, secret = (
        cfg.registry_host,
        cfg.registry_robot_name,
        cfg.registry_robot_secret,
    )
    if not (host and robot and secret):
        return None
    await get_orchestrator().ensure_pull_secret(
        namespace,
        dockerconfigjson(host, robot, secret),
        pull_secret_fingerprint(host, robot, secret),
    )
    return PULL_SECRET_NAME
