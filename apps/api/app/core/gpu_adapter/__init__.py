"""GPU 资源申请抽象层:device-plugin 语法(HAMi 软切分 / MIG / 整卡直通)。

**派发键是节点池,不是档位。** 池标签装机时定死、是隔离机制的物理事实源;档位(`skus.tier`)
只表达售卖分类。两者曾各自承载一半机制判断(tier 决定资源语法、pool 决定 nodeSelector),
于是「同一份档位元组」在 catalog / orchestrator / metering / 本模块各抄了一遍,且天然可能对不齐。

分池铁律:
- kata → Kata(RuntimeClass=kata-qemu)+ VFIO 整卡直通,不叠 userns(VM 级隔离)
- mig  → runc + MIG device plugin + userns 加固(hostUsers=false)
- hami → runc + HAMi 软切分 + userns 加固(hostUsers=false)
- cpu  → runc + userns 加固,不申请任何 nvidia.com/* 资源
Kata 与 HAMi 永不混布同一节点池。

**gpu_count == 0(CPU 实例)先于池分支判定**:CPU 档允许挂 hami 池跑 GPU 机的空闲
CPU,若仍按池分支走,就会替一台没有 GPU 的实例申请 nvidia.com/gpu,占掉真正卖卡的名额。
"""

from dataclasses import dataclass, field
from typing import Any

from app.core.k8s.base import GPU_MODEL_NODE_LABEL, POOL_NODE_LABEL

# HAMi 型号白名单 annotation,值须为 HAMi 登记的原文串(nvidia-smi 名),canonical 不同构
HAMI_USE_GPUTYPE_ANNOTATION = "nvidia.com/use-gputype"

# ---------- 节点池(隔离机制的事实源) ----------
POOL_KATA = "kata"
POOL_MIG = "mig"
POOL_HAMI = "hami"
POOL_CPU = "cpu"  # 无卡节点池(纯 CPU 实例;GPU 节点的空闲 CPU 走 hami 池)

# ---------- 售卖档位(纯商业分类;标准/经济由所在池派生,不再单列枚举值) ----------
TIER_DEDICATED = "dedicated"  # 专用整卡 → kata 池
TIER_SHARED = "shared"  # 共享切分 → mig 池(标准,硬切分)或 hami 池(经济,软切分超卖)
TIER_CPU = "cpu"  # 纯 CPU,不带卡 → cpu 池(无卡机)或 hami 池(GPU 机的空闲 CPU)
TIERS = (TIER_DEDICATED, TIER_SHARED, TIER_CPU)

# 档位 → 允许落的池。派发键改成池之后,这张表就是「档位承诺的隔离强度」与「实际跑在哪」
# 之间的唯一约束:没有它,运营可以建出 tier=dedicated 却挂 hami 池的 SKU——
# 卖的是整卡直通,跑的是软切分超卖。建 SKU 与改池两条路径都过 catalog 的同一处校验。
# cpu 档允许挂 hami 池,是为了没有无卡服务器时也能先上线:CPU 实例不申请 nvidia.com/*,
# 只吃 GPU 节点的空闲 CPU;吃多少由策略 gpu_node_cpu_instance_vcpu_cap 封顶(0 = 不许)。
TIER_POOLS: dict[str, tuple[str, ...]] = {
    TIER_DEDICATED: (POOL_KATA,),
    TIER_SHARED: (POOL_MIG, POOL_HAMI),
    TIER_CPU: (POOL_CPU, POOL_HAMI),
}


@dataclass(frozen=True)
class GpuRequest:
    resources: dict[str, str]  # container resources.limits 增量
    runtime_class: str | None  # RuntimeClass 名称
    host_users: bool  # False → pod.spec.hostUsers=false(userns)
    node_selector: dict[str, str]
    # HAMi 池必须显式走 hami-scheduler:其 mutating webhook failurePolicy=Ignore,不可依赖
    scheduler_name: str | None = None
    annotations: dict[str, str] = field(default_factory=dict)  # pod metadata.annotations 增量


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
    """gpu_count=0 即 CPU 实例(不申请任何 nvidia.com/*,不钉型号),判定先于池分支;
    gpu_model 为 canonical 型号(节点巡检打的 label 值),有值则全池钉型号;
    hami_gputype 为原文串,仅 hami 池注 use-gputype annotation;
    distro=k3s 时 hami 池必须显式 runtimeClassName=nvidia(k3s 不设默认运行时;
    RKE2+gpu-operator 默认已是 nvidia,故为 None)。"""
    node_selector = {POOL_NODE_LABEL: pool_label}
    if gpu_count == 0:
        # CPU 实例:必须先于池分支返回。cpu 档可以挂 hami 池,落到下面的池分支就会申请
        # nvidia.com/gpu(还带 gpucores/gpumem 限额),把一张真卡判给不用卡的实例。
        # 不钉 superdl.io/gpu-model:无卡节点根本没有这个标签,钉了必然 Pending。
        return GpuRequest(
            resources={},
            runtime_class=None,
            host_users=False,  # runc + userns 加固,与 hami/mig 池同款
            node_selector=node_selector,
        )
    if gpu_model:
        node_selector[GPU_MODEL_NODE_LABEL] = gpu_model
    if pool_label == POOL_KATA:
        return GpuRequest(
            resources={"nvidia.com/gpu": str(gpu_count)},
            runtime_class="kata-qemu",
            host_users=True,  # Kata 为 VM 级隔离,不叠 userns
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
        # HAMi:gpu 数 + 算力百分比 + 显存 MB(CUDA 层限额)
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
