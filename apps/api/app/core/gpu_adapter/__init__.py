"""GPU 资源申请抽象层。

当前用 device-plugin 语法(HAMi 软切分 / MIG / 整卡直通);DRA 迁移只需改此层
(见 development-plan §3.3)。

分池铁律:
- dedicated → Kata 4.0(RuntimeClass=kata-qemu)+ VFIO 整卡直通,kata 池
- mig       → runc + MIG device plugin,mig 池
- shared_*  → runc + HAMi 软切分 + userns 加固(hostUsers=false),hami 池
Kata 与 HAMi 永不混布同一节点池。
"""

from dataclasses import dataclass
from typing import Any

POOL_NODE_LABEL = "superdl.io/pool"


@dataclass(frozen=True)
class GpuRequest:
    resources: dict[str, str]  # container resources.limits 增量
    runtime_class: str | None  # RuntimeClass 名称
    host_users: bool  # False → pod.spec.hostUsers=false(userns)
    node_selector: dict[str, str]
    # HAMi 池显式走 hami-scheduler(不依赖 mutating webhook,其 failurePolicy=Ignore)
    scheduler_name: str | None = None


def build_gpu_request(
    *,
    tier: str,
    gpu_count: int,
    gpu_cores_pct: int,
    vram_gb: int,
    mig_profile: str | None,
    pool_label: str,
) -> GpuRequest:
    node_selector = {POOL_NODE_LABEL: pool_label}
    if tier == "dedicated":
        return GpuRequest(
            resources={"nvidia.com/gpu": str(gpu_count)},
            runtime_class="kata-qemu",
            host_users=True,  # Kata 本身是 VM 级隔离,无需 userns
            node_selector=node_selector,
        )
    if tier == "mig":
        if not mig_profile:
            raise ValueError("mig tier requires mig_profile")
        return GpuRequest(
            resources={f"nvidia.com/mig-{mig_profile}": str(gpu_count)},
            runtime_class=None,
            host_users=True,
            node_selector=node_selector,
        )
    if tier in ("shared_std", "shared_eco"):
        # HAMi:gpu 数 + 算力百分比 + 显存 MB(CUDA 层限额)
        return GpuRequest(
            resources={
                "nvidia.com/gpu": str(gpu_count),
                "nvidia.com/gpucores": str(gpu_cores_pct),
                "nvidia.com/gpumem": str(vram_gb * 1024),
            },
            runtime_class=None,
            host_users=False,  # 共享池必须 userns 加固
            node_selector=node_selector,
            scheduler_name="hami-scheduler",  # 显式指定,不赖 HAMi mutating webhook(fail-open)
        )
    raise ValueError(f"unknown tier: {tier}")


def spec_to_gpu_request(spec: dict[str, Any], gpu_count: int) -> GpuRequest:
    """从实例的 SKU 快照构造。"""
    return build_gpu_request(
        tier=spec["tier"],
        gpu_count=gpu_count,
        gpu_cores_pct=spec.get("gpu_cores_pct", 100),
        vram_gb=spec["vram_gb"],
        mig_profile=spec.get("mig_profile"),
        pool_label=spec["pool_label"],
    )
