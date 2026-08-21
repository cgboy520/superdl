"""GPU 资源申请抽象层。

当前用 device-plugin 语法(HAMi 软切分 / MIG / 整卡直通);DRA 迁移只需改此层
(见 development-plan §3.3)。

分池铁律:
- dedicated → Kata 4.0(RuntimeClass=kata-qemu)+ VFIO 整卡直通,kata 池
- mig       → runc + MIG device plugin + userns 加固(hostUsers=false),mig 池
- shared_*  → runc + HAMi 软切分 + userns 加固(hostUsers=false),hami 池
Kata 与 HAMi 永不混布同一节点池。
"""

from dataclasses import dataclass, field
from typing import Any

from app.core.k8s.base import GPU_MODEL_NODE_LABEL

POOL_NODE_LABEL = "superdl.io/pool"
# HAMi 型号白名单 annotation,值须为 HAMi 登记的原文串(nvidia-smi 名),canonical 不同构
HAMI_USE_GPUTYPE_ANNOTATION = "nvidia.com/use-gputype"


@dataclass(frozen=True)
class GpuRequest:
    resources: dict[str, str]  # container resources.limits 增量
    runtime_class: str | None  # RuntimeClass 名称
    host_users: bool  # False → pod.spec.hostUsers=false(userns)
    node_selector: dict[str, str]
    # HAMi 池显式走 hami-scheduler(不依赖 mutating webhook,其 failurePolicy=Ignore)
    scheduler_name: str | None = None
    annotations: dict[str, str] = field(default_factory=dict)  # pod metadata.annotations 增量


def build_gpu_request(
    *,
    tier: str,
    gpu_count: int,
    gpu_cores_pct: int,
    vram_gb: int,
    mig_profile: str | None,
    pool_label: str,
    gpu_model: str | None = None,
    hami_gputype: str | None = None,
    distro: str | None = None,
) -> GpuRequest:
    """gpu_model 为 canonical 型号(节点巡检打的 label 值),有值则全档位钉型号;
    hami_gputype 为原文串,仅共享档注 use-gputype annotation(混卡节点兜底,默认关);
    distro=k3s 时共享档显式 runtimeClassName=nvidia(k3s 只探测 nvidia 运行时不设默认,
    RKE2+gpu-operator 默认运行时已是 nvidia 故保持 None)。"""
    node_selector = {POOL_NODE_LABEL: pool_label}
    if gpu_model:
        node_selector[GPU_MODEL_NODE_LABEL] = gpu_model
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
            host_users=False,  # 与共享池同为 runc,同样要 userns 加固(缩小逃逸落点)
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
            runtime_class="nvidia" if distro == "k3s" else None,
            host_users=False,  # 共享池必须 userns 加固
            node_selector=node_selector,
            scheduler_name="hami-scheduler",  # 显式指定,不赖 HAMi mutating webhook(fail-open)
            annotations={HAMI_USE_GPUTYPE_ANNOTATION: hami_gputype} if hami_gputype else {},
        )
    raise ValueError(f"unknown tier: {tier}")


def spec_to_gpu_request(
    spec: dict[str, Any],
    gpu_count: int,
    *,
    hami_use_gputype: bool = False,
    distro: str | None = None,
) -> GpuRequest:
    """从实例的 SKU 快照构造。存量快照无 gpu_model_selector 键 → 天然不加型号约束。"""
    return build_gpu_request(
        tier=spec["tier"],
        gpu_count=gpu_count,
        gpu_cores_pct=spec.get("gpu_cores_pct", 100),
        vram_gb=spec["vram_gb"],
        mig_profile=spec.get("mig_profile"),
        pool_label=spec["pool_label"],
        gpu_model=spec.get("gpu_model_selector"),
        hami_gputype=spec.get("gpu_model") if hami_use_gputype else None,
        distro=distro,
    )
