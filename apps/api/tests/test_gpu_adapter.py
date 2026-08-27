"""gpu_adapter:型号约束(nodeSelector)与 HAMi use-gputype annotation 的构造规则。"""

import pytest

from app.core.config import get_settings
from app.core.gpu_adapter import (
    HAMI_USE_GPUTYPE_ANNOTATION,
    POOL_NODE_LABEL,
    build_gpu_request,
    spec_to_gpu_request,
)
from app.core.k8s.base import GPU_MODEL_NODE_LABEL
from app.modules.orchestrator.models import Instance
from app.modules.orchestrator.service import _encode_token, build_pod_spec


def _spec(tier: str, pool: str, **extra):
    base = {
        "tier": tier,
        "pool_label": pool,
        "vram_gb": 24,
        "gpu_cores_pct": 50,
        "mig_profile": "1g.10gb" if pool == "mig" else None,
        "vcpu": 8,
        "mem_gb": 32,
        "disk_gb": 100,
        "gpu_model": "NVIDIA GeForce RTX 4090",
    }
    base.update(extra)
    return base


def test_gpu_model_pins_node_selector():
    """有 canonical 型号即钉 superdl.io/gpu-model(型号约束在分池之前,与池无关)。"""
    req = build_gpu_request(
        gpu_count=1,
        gpu_cores_pct=100,
        vram_gb=24,
        mig_profile=None,
        pool_label="kata",
        gpu_model="RTX4090",
    )
    assert req.node_selector == {POOL_NODE_LABEL: "kata", GPU_MODEL_NODE_LABEL: "RTX4090"}


def test_no_gpu_model_keeps_pool_only_selector():
    req = build_gpu_request(
        gpu_count=1,
        gpu_cores_pct=50,
        vram_gb=24,
        mig_profile=None,
        pool_label="hami",
    )
    assert req.node_selector == {POOL_NODE_LABEL: "hami"}
    assert req.annotations == {}


def test_snapshot_selector_pins_model():
    req = spec_to_gpu_request(_spec("dedicated", "kata", gpu_model_selector="H100-80G"), 2)
    assert req.node_selector[GPU_MODEL_NODE_LABEL] == "H100-80G"
    assert req.resources == {"nvidia.com/gpu": "2"}


def test_hami_gputype_annotation_hami_pool_only_and_raw_value():
    """开关开启时仅 hami 池注 annotation,且值为 SKU 原文串(非 canonical)。"""
    spec = _spec("shared", "hami", gpu_model_selector="RTX4090")
    on = spec_to_gpu_request(spec, 1, hami_use_gputype=True)
    assert on.annotations == {HAMI_USE_GPUTYPE_ANNOTATION: "NVIDIA GeForce RTX 4090"}
    off = spec_to_gpu_request(spec, 1)
    assert off.annotations == {}
    dedicated = spec_to_gpu_request(
        _spec("dedicated", "kata", gpu_model_selector="RTX4090"), 1, hami_use_gputype=True
    )
    assert dedicated.annotations == {}


def _instance(spec: dict) -> Instance:
    return Instance(
        user_id=1,
        uuid="i-test",
        image_ref="img:latest",
        spec=spec,
        gpu_count=1,
        ssh_port=30022,
        jupyter_token=_encode_token("tok", instance_uuid="i-test"),
        authorized_keys=[],
        data_disk_id=None,
        k8s_namespace="tenant-1",
        status="creating",
        sku_id=1,
    )


def test_build_pod_spec_carries_selector_and_annotations(monkeypatch):
    monkeypatch.setattr(get_settings(), "hami_use_gputype", True)
    pod = build_pod_spec(_instance(_spec("shared", "hami", gpu_model_selector="RTX4090")))
    assert pod.node_selector[GPU_MODEL_NODE_LABEL] == "RTX4090"
    assert pod.annotations == {HAMI_USE_GPUTYPE_ANNOTATION: "NVIDIA GeForce RTX 4090"}


def test_build_pod_spec_multi_gpu_scales_cpu_mem():
    """N 卡实例 Pod limits = N × SKU(收 N 倍价即给 N 份资源);系统盘不放大。"""
    inst = _instance(_spec("dedicated", "kata"))
    inst.gpu_count = 8
    pod = build_pod_spec(inst)
    assert pod.vcpu == 8 * 8  # SKU 8 vCPU/卡 × 8 卡
    assert pod.mem_gb == 32 * 8
    assert pod.disk_gb == 100
    single = build_pod_spec(_instance(_spec("dedicated", "kata")))
    assert single.vcpu == 8 and single.mem_gb == 32


@pytest.mark.parametrize(("pool", "host_users"), [("kata", True), ("mig", False), ("hami", False)])
def test_userns_hardening_by_pool(pool: str, host_users: bool):
    """runc 池(mig / hami)一律 hostUsers=false;kata 池走 Kata 的 VM 级隔离。"""
    req = build_gpu_request(
        gpu_count=1,
        gpu_cores_pct=50,
        vram_gb=24,
        mig_profile="1g.10gb" if pool == "mig" else None,
        pool_label=pool,
    )
    assert req.host_users is host_users


def test_unknown_pool_is_fail_closed():
    """池标签认不出就不下发 —— 宁可 500 也不建出无 GPU 资源请求的 Pod。

    档位迁移(mig / shared_std / shared_eco → shared)后,旧值若残留在 spec 快照里
    会走到这里;迁移一次改到位,不留别名兜底。
    """
    with pytest.raises(ValueError, match="unknown pool"):
        build_gpu_request(
            gpu_count=1,
            gpu_cores_pct=50,
            vram_gb=24,
            mig_profile=None,
            pool_label="shared_std",
        )
