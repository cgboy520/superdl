"""GPU 资源申请抽象层:按节点池派发 device-plugin 语法(档位只表达售卖分类)。

- kata → RuntimeClass=kata-qemu + VFIO 整卡直通,不叠 userns
- mig  → runc + MIG device plugin + hostUsers=false(硬件隔离)
- hami → runc + HAMi 软切分 + hostUsers=false(软件限额,非安全边界,见 docs/reference/security.md)
- cpu  → runc + hostUsers=false,不申请 nvidia.com/*
Kata 与 HAMi 不混布同一节点池;gpu_count == 0 先于池分支判定。
"""

from dataclasses import dataclass, field
from typing import Any

from app.core.k8s.base import (
    GPU_DEPLOY_DEVICE_PLUGIN_LABEL,
    GPU_MODEL_NODE_LABEL,
    GPU_WORKLOAD_CONFIG_LABEL,
    LEGACY_POOL_NODE_LABEL,
    POOL_NODE_LABEL,
)

HAMI_USE_GPUTYPE_ANNOTATION = "nvidia.com/use-gputype"

POOL_KATA = "kata"
POOL_MIG = "mig"
POOL_HAMI = "hami"
POOL_CPU = "cpu"

TIER_DEDICATED = "dedicated"
TIER_SHARED = "shared"
TIER_CPU = "cpu"
TIERS = (TIER_DEDICATED, TIER_SHARED, TIER_CPU)

SWITCHABLE_POOLS: tuple[str, ...] = (POOL_KATA, POOL_HAMI, POOL_MIG)

TIER_POOLS: dict[str, tuple[str, ...]] = {
    TIER_DEDICATED: (POOL_KATA,),
    TIER_SHARED: (POOL_MIG, POOL_HAMI),
    TIER_CPU: (POOL_CPU, POOL_HAMI),
}


@dataclass(frozen=True)
class GpuRequest:
    resources: dict[str, str]
    runtime_class: str | None
    host_users: bool
    node_selector: dict[str, str]
    scheduler_name: str | None = None
    annotations: dict[str, str] = field(default_factory=dict)


def build_gpu_request(
    *,
    pool_label: str,
    gpu_count: int,
    gpu_cores_pct: int,
    vram_gb: int,
    mig_profile: str | None,
    gpu_model: str | None = None,
    hami_gputype: str | None = None,
    distro: str | None = None,
) -> GpuRequest:
    """gpu_count=0 即 CPU 实例(不申请 nvidia.com/*,不钉型号);gpu_model 有值则钉型号;
    hami_gputype 仅 hami 池注 annotation;distro=k3s 时 hami 池显式 runtimeClassName=nvidia。"""
    node_selector = {POOL_NODE_LABEL: pool_label}
    if gpu_count == 0:
        return GpuRequest(
            resources={},
            runtime_class=None,
            host_users=False,
            node_selector=node_selector,
        )
    if gpu_model:
        node_selector[GPU_MODEL_NODE_LABEL] = gpu_model
    if pool_label == POOL_KATA:
        return GpuRequest(
            resources={"nvidia.com/gpu": str(gpu_count)},
            runtime_class="kata-qemu",
            host_users=True,
            node_selector=node_selector,
        )
    if pool_label == POOL_MIG:
        if not mig_profile:
            raise ValueError("mig pool requires mig_profile")
        return GpuRequest(
            resources={f"nvidia.com/mig-{mig_profile}": str(gpu_count)},
            runtime_class=None,
            host_users=False,
            node_selector=node_selector,
        )
    if pool_label == POOL_HAMI:
        return GpuRequest(
            resources={
                "nvidia.com/gpu": str(gpu_count),
                "nvidia.com/gpucores": str(gpu_cores_pct),
                "nvidia.com/gpumem": str(vram_gb * 1024),
            },
            runtime_class="nvidia" if distro == "k3s" else None,
            host_users=False,
            node_selector=node_selector,
            scheduler_name="hami-scheduler",
            annotations={HAMI_USE_GPUTYPE_ANNOTATION: hami_gputype} if hami_gputype else {},
        )
    raise ValueError(f"unknown pool: {pool_label}")


def pool_node_labels(pool_label: str) -> dict[str, str | None]:
    """返回池标签的完整期望集(None 表示删除,含老键);未知池抛 ValueError。池标签只允许平台写入。"""
    if pool_label not in (POOL_KATA, POOL_HAMI, POOL_MIG, POOL_CPU):
        raise ValueError(f"unknown pool: {pool_label}")
    return {
        POOL_NODE_LABEL: pool_label,
        LEGACY_POOL_NODE_LABEL: None,
        GPU_WORKLOAD_CONFIG_LABEL: "vm-passthrough" if pool_label == POOL_KATA else None,
        GPU_DEPLOY_DEVICE_PLUGIN_LABEL: "false" if pool_label == POOL_HAMI else None,
    }


def spec_to_gpu_request(
    spec: dict[str, Any],
    gpu_count: int,
    *,
    hami_use_gputype: bool = False,
    distro: str | None = None,
) -> GpuRequest:
    """从实例的 SKU 快照构造(gpu_model_selector 为 None = 不钉型号)。"""
    return build_gpu_request(
        pool_label=spec["pool_label"],
        gpu_count=gpu_count,
        gpu_cores_pct=spec.get("gpu_cores_pct", 100),
        vram_gb=spec["vram_gb"],
        mig_profile=spec.get("mig_profile"),
        gpu_model=spec.get("gpu_model_selector"),
        hami_gputype=spec.get("gpu_model") if hami_use_gputype else None,
        distro=distro,
    )
