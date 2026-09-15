"""gpu_adapter:型号约束(nodeSelector)与 HAMi use-gputype annotation 的构造规则。"""

import pytest

from app.core.config import get_settings
from app.core.gpu_adapter import (
    HAMI_USE_GPUTYPE_ANNOTATION,
    POOL_NODE_LABEL,
    build_gpu_request,
    pool_node_labels,
    spec_to_gpu_request,
)
from app.core.k8s.base import (
    GPU_DEPLOY_DEVICE_PLUGIN_LABEL,
    GPU_MODEL_NODE_LABEL,
    GPU_WORKLOAD_CONFIG_LABEL,
    LEGACY_POOL_NODE_LABEL,
)
from app.modules.orchestrator.service import build_pod_spec
from tests.helpers import gpu_spec, make_instance


def test_pool_label_lives_under_node_restriction_prefix():
    """池标签键在 NodeRestriction 保护前缀下,kubelet --node-label 打不上;老键随收敛删除。"""
    assert POOL_NODE_LABEL.startswith("node-restriction.kubernetes.io/")
    assert LEGACY_POOL_NODE_LABEL == "superdl.io/pool"
    labels = pool_node_labels("hami")
    assert labels[POOL_NODE_LABEL] == "hami" and labels[LEGACY_POOL_NODE_LABEL] is None
    assert labels[GPU_DEPLOY_DEVICE_PLUGIN_LABEL] == "false"
    assert labels[GPU_WORKLOAD_CONFIG_LABEL] is None
    with pytest.raises(ValueError):
        pool_node_labels("gpu")


def test_gpu_model_pins_node_selector():
    """有 canonical 型号即钉 superdl.io/gpu-model,与池无关。"""
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
    req = spec_to_gpu_request(gpu_spec("dedicated", "kata", gpu_model_selector="H100-80G"), 2)
    assert req.node_selector[GPU_MODEL_NODE_LABEL] == "H100-80G"
    assert req.resources == {"nvidia.com/gpu": "2"}


def test_hami_gputype_annotation_hami_pool_only_and_raw_value():
    """开关开启时仅 hami 池注 annotation,且值为 SKU 原文串(非 canonical)。"""
    spec = gpu_spec("shared", "hami", gpu_model_selector="RTX4090")
    on = spec_to_gpu_request(spec, 1, hami_use_gputype=True)
    assert on.annotations == {HAMI_USE_GPUTYPE_ANNOTATION: "NVIDIA GeForce RTX 4090"}
    off = spec_to_gpu_request(spec, 1)
    assert off.annotations == {}
    dedicated = spec_to_gpu_request(
        gpu_spec("dedicated", "kata", gpu_model_selector="RTX4090"), 1, hami_use_gputype=True
    )
    assert dedicated.annotations == {}


def test_build_pod_spec_carries_selector_and_annotations(monkeypatch):
    monkeypatch.setattr(get_settings(), "hami_use_gputype", True)
    pod = build_pod_spec(
        make_instance(spec=gpu_spec("shared", "hami", gpu_model_selector="RTX4090"))
    )
    assert pod.node_selector[GPU_MODEL_NODE_LABEL] == "RTX4090"
    assert pod.annotations[HAMI_USE_GPUTYPE_ANNOTATION] == "NVIDIA GeForce RTX 4090"
    assert pod.annotations["kubernetes.io/egress-bandwidth"] == "200M"
    assert "kubernetes.io/ingress-bandwidth" not in pod.annotations


def test_build_pod_spec_multi_gpu_scales_cpu_mem():
    """N 卡实例 Pod limits = N × SKU;系统盘不放大。"""
    inst = make_instance(spec=gpu_spec("dedicated", "kata"))
    inst.gpu_count = 8
    pod = build_pod_spec(inst)
    assert pod.vcpu == 8 * 8
    assert pod.mem_gb == 32 * 8
    assert pod.disk_gb == 100
    single = build_pod_spec(make_instance(spec=gpu_spec("dedicated", "kata")))
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
    """未知池/档位即抛错,不下发。"""
    with pytest.raises(ValueError, match="unknown pool"):
        build_gpu_request(
            gpu_count=1,
            gpu_cores_pct=50,
            vram_gb=24,
            mig_profile=None,
            pool_label="shared_std",
        )
