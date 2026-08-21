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
from app.modules.orchestrator.service import build_pod_spec


def _spec(tier: str, pool: str, **extra):
    base = {
        "tier": tier,
        "pool_label": pool,
        "vram_gb": 24,
        "gpu_cores_pct": 50,
        "mig_profile": "1g.10gb" if tier == "mig" else None,
        "vcpu": 8,
        "mem_gb": 32,
        "disk_gb": 100,
        "gpu_model": "NVIDIA GeForce RTX 4090",
    }
    base.update(extra)
    return base


@pytest.mark.parametrize(
    ("tier", "pool"), [("dedicated", "kata"), ("mig", "mig"), ("shared_std", "hami")]
)
def test_gpu_model_pins_node_selector_all_tiers(tier: str, pool: str):
    req = build_gpu_request(
        tier=tier,
        gpu_count=1,
        gpu_cores_pct=50,
        vram_gb=24,
        mig_profile="1g.10gb" if tier == "mig" else None,
        pool_label=pool,
        gpu_model="RTX4090",
    )
    assert req.node_selector == {POOL_NODE_LABEL: pool, GPU_MODEL_NODE_LABEL: "RTX4090"}


def test_no_gpu_model_keeps_pool_only_selector():
    req = build_gpu_request(
        tier="shared_std",
        gpu_count=1,
        gpu_cores_pct=50,
        vram_gb=24,
        mig_profile=None,
        pool_label="hami",
    )
    assert req.node_selector == {POOL_NODE_LABEL: "hami"}
    assert req.annotations == {}


def test_legacy_snapshot_without_selector_key_unaffected():
    """存量实例快照没有 gpu_model_selector 键 → 不加型号约束(重启/重建不变更调度)。"""
    req = spec_to_gpu_request(_spec("shared_std", "hami"), 1)
    assert GPU_MODEL_NODE_LABEL not in req.node_selector
    assert req.annotations == {}


def test_snapshot_selector_none_means_unrecognized_model():
    req = spec_to_gpu_request(_spec("shared_std", "hami", gpu_model_selector=None), 1)
    assert GPU_MODEL_NODE_LABEL not in req.node_selector


def test_snapshot_selector_pins_model():
    req = spec_to_gpu_request(_spec("dedicated", "kata", gpu_model_selector="H100-80G"), 2)
    assert req.node_selector[GPU_MODEL_NODE_LABEL] == "H100-80G"
    assert req.resources == {"nvidia.com/gpu": "2"}


def test_hami_gputype_annotation_shared_only_and_raw_value():
    """开关开启时仅共享档注 annotation,且值为 SKU 原文串(非 canonical)。"""
    spec = _spec("shared_std", "hami", gpu_model_selector="RTX4090")
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
        jupyter_token="tok",
        authorized_keys=[],
        data_disk_id=None,
        k8s_namespace="tenant-1",
        status="creating",
        sku_id=1,
    )


def test_build_pod_spec_carries_selector_and_annotations(monkeypatch):
    monkeypatch.setattr(get_settings(), "hami_use_gputype", True)
    pod = build_pod_spec(_instance(_spec("shared_std", "hami", gpu_model_selector="RTX4090")))
    assert pod.node_selector[GPU_MODEL_NODE_LABEL] == "RTX4090"
    assert pod.annotations == {HAMI_USE_GPUTYPE_ANNOTATION: "NVIDIA GeForce RTX 4090"}


def test_build_pod_spec_default_flag_off_no_annotations():
    pod = build_pod_spec(_instance(_spec("shared_std", "hami", gpu_model_selector="RTX4090")))
    assert pod.annotations == {}


@pytest.mark.parametrize(
    ("tier", "host_users"),
    [("dedicated", True), ("mig", False), ("shared_std", False), ("shared_eco", False)],
)
def test_userns_hardening_by_tier(tier: str, host_users: bool):
    """runc 档(mig / shared_*)一律 hostUsers=false;dedicated 走 Kata 的 VM 级隔离。"""
    req = build_gpu_request(
        tier=tier,
        gpu_count=1,
        gpu_cores_pct=50,
        vram_gb=24,
        mig_profile="1g.10gb" if tier == "mig" else None,
        pool_label=tier,
    )
    assert req.host_users is host_users
