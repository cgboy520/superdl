"""Real K8s objects and disk lifecycle; skipped unless SUPERDL_TEST_KUBECONFIG is set."""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from kubernetes import client as k8s_client

from app.core.config import get_settings
from app.core.k8s.base import (
    GATEWAY_API_GROUP,
    GATEWAY_API_VERSION,
    GATEWAY_APP_LISTENER,
    GATEWAY_NAME,
    HTTPROUTE_PLURAL,
    InstancePodSpec,
    data_disk_pvc_name,
    instance_disk_pvc_name,
    jupyter_service_name,
)
from app.core.k8s.real import MANAGED_LABEL, PRIVATE_CIDRS, RealOrchestrator
from tests.helpers import use_kubeconfig, wait_pvc_bound

pytestmark = [
    pytest.mark.real_k8s,
    pytest.mark.skipif(
        not os.environ.get("SUPERDL_TEST_KUBECONFIG"),
        reason="SUPERDL_TEST_KUBECONFIG unset, skipping the real K8s smoke",
    ),
]

POD_GONE_TIMEOUT = 30.0
PVC_GONE_TIMEOUT = 30.0


@pytest.fixture(scope="module")
def orch() -> RealOrchestrator:
    """RealOrchestrator against the cluster; SUPERDL_TEST_KUBECONFIG becomes the standard KUBECONFIG
    for the client."""
    use_kubeconfig(os.environ["SUPERDL_TEST_KUBECONFIG"])
    return RealOrchestrator()


@pytest.fixture(scope="module")
def orch_restricted() -> RealOrchestrator:
    """Orchestrator under the restricted identity (superdl-tenant-mgr SA): skipped without
    SUPERDL_TEST_KUBECONFIG_RESTRICTED."""
    path = os.environ.get("SUPERDL_TEST_KUBECONFIG_RESTRICTED")
    if not path:
        pytest.skip(
            "SUPERDL_TEST_KUBECONFIG_RESTRICTED unset (only the CI kind job injects the restricted"
            " identity)"
        )
    use_kubeconfig(path)
    return RealOrchestrator()


@pytest.fixture
async def namespace(orch: RealOrchestrator) -> AsyncIterator[str]:
    """One dedicated tenant ns per case, deleted at the end."""
    ns = f"tenant-smoke-{uuid.uuid4().hex[:8]}"
    await orch.ensure_namespace(ns)
    yield ns
    try:
        await asyncio.to_thread(orch.core.delete_namespace, ns)
    except k8s_client.ApiException as exc:
        if exc.status != 404:
            raise


async def _wait_pod_gone(orch: RealOrchestrator, namespace: str, name: str) -> None:
    deadline = asyncio.get_running_loop().time() + POD_GONE_TIMEOUT
    while True:
        status = await orch.get_status(namespace, name)
        if not status.exists:
            return
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError(f"pod {name} deletion timed out")
        await asyncio.sleep(0.5)


async def _wait_pvc_gone(orch: RealOrchestrator, namespace: str, pvc_name: str) -> None:
    """Wait for the PVC to really vanish (404 only once the finalizers are cleared)."""
    deadline = asyncio.get_running_loop().time() + PVC_GONE_TIMEOUT
    while True:
        try:
            orch.core.read_namespaced_persistent_volume_claim(pvc_name, namespace)
        except k8s_client.ApiException as exc:
            if exc.status == 404:
                return
            raise
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError(f"pvc {pvc_name} deletion timed out")
        await asyncio.sleep(0.5)


async def test_namespace_security_baseline(orch: RealOrchestrator, namespace: str) -> None:
    """Tenant namespace PSA, NetworkPolicy, quota and per-disk PVC apply idempotently."""
    await orch.ensure_namespace(namespace)

    ns: Any = orch.core.read_namespace(namespace)
    assert ns.metadata.labels[MANAGED_LABEL] == "true"
    assert ns.metadata.labels["pod-security.kubernetes.io/enforce"] == "baseline"
    assert ns.metadata.labels["pod-security.kubernetes.io/audit"] == "restricted"

    netpol: Any = orch.net.read_namespaced_network_policy("tenant-default", namespace)
    spec = netpol.spec
    assert spec is not None and set(spec.policy_types) == {"Ingress", "Egress"}
    assert spec.egress is not None and len(spec.egress) == 3
    tcp_rule = spec.egress[1]
    assert tcp_rule.ports is not None and any(p.end_port for p in tcp_rule.ports)
    assert tcp_rule.to is not None and tcp_rule.to[0].ip_block is not None
    assert set(tcp_rule.to[0].ip_block._except or []) == set(PRIVATE_CIDRS)
    assert spec.ingress is not None and len(spec.ingress) == 2
    ssh_block = spec.ingress[1]._from[0].ip_block
    assert ssh_block is not None and ssh_block.cidr == "0.0.0.0/0"
    assert ssh_block._except == [get_settings().tenant_pod_cidr]

    quota: Any = orch.core.read_namespaced_resource_quota("tenant-quota", namespace)
    assert quota.spec is not None and "pods" in quota.spec.hard
    assert "requests.cpu" in quota.spec.hard and "limits.ephemeral-storage" in quota.spec.hard

    pvc_name = data_disk_pvc_name("a" * 32)
    await orch.ensure_data_disk(namespace, pvc_name, 10)
    created: Any = orch.core.read_namespaced_persistent_volume_claim(pvc_name, namespace)
    assert created.spec.resources.requests["storage"] == "10Gi"
    assert created.spec.access_modes == ["ReadWriteMany"]
    await orch.ensure_data_disk(namespace, pvc_name, 10)
    await wait_pvc_bound(orch.core, namespace, pvc_name)
    await orch.ensure_data_disk(namespace, pvc_name, 20)
    grown: Any = orch.core.read_namespaced_persistent_volume_claim(pvc_name, namespace)
    assert grown.spec.resources.requests["storage"] == "20Gi"
    await orch.ensure_data_disk(namespace, pvc_name, 5)
    same: Any = orch.core.read_namespaced_persistent_volume_claim(pvc_name, namespace)
    assert same.spec.resources.requests["storage"] == "20Gi"
    await orch.delete_data_disk(namespace, pvc_name)
    await orch.delete_data_disk(namespace, pvc_name)


async def test_ensure_namespace_under_tenant_mgr_sa(orch_restricted: RealOrchestrator) -> None:
    """The superdl-tenant-mgr identity applies tenant namespace resources idempotently."""
    from kubernetes import config as k8s_config

    ns = f"tenant-rbac-{uuid.uuid4().hex[:8]}"
    await orch_restricted.ensure_namespace(ns)
    try:
        await orch_restricted.ensure_namespace(ns)
        ns_obj: Any = orch_restricted.core.read_namespace(ns)
        assert ns_obj.metadata.labels[MANAGED_LABEL] == "true"
        orch_restricted.core.read_namespaced_resource_quota("tenant-quota", ns)
        orch_restricted.core.read_namespaced_limit_range("tenant-defaults", ns)
        orch_restricted.net.read_namespaced_network_policy("tenant-default", ns)
    finally:
        k8s_config.load_kube_config(config_file=os.environ["SUPERDL_TEST_KUBECONFIG"])
        await asyncio.to_thread(k8s_client.CoreV1Api().delete_namespace, ns)


async def test_instance_lifecycle_and_disk_reclaim(orch: RealOrchestrator, namespace: str) -> None:
    """Full instance lifecycle: create (idempotent) → hardening assertions → delete the instance,
    keep the disk → reclaim the disk explicitly."""
    name = f"smoke-{uuid.uuid4().hex[:12]}"
    spec = InstancePodSpec(
        namespace=namespace,
        name=name,
        image="registry.invalid/smoke:0",
        gpu_resources={},
        runtime_class=None,
        host_users=True,
        vcpu=1,
        mem_gb=1,
        disk_gb=1,
        ssh_node_port=31999,
        jupyter_host=f"{name}.app.example.invalid",
        secret_env={"JUPYTER_TOKEN": "smoke"},
        authorized_keys=(
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeFakeFakeFakeFakeFakeFakeFakeFak smoke",
        ),
    )
    await orch.create_instance(spec)
    await orch.create_instance(spec)

    status = await orch.get_status(namespace, name)
    assert status.exists and not status.ready and not status.deleting

    pod: Any = orch.core.read_namespaced_pod(name, namespace)
    assert pod.spec is not None
    token_env = next(e for e in pod.spec.containers[0].env if e.name == "JUPYTER_TOKEN")
    assert token_env.value is None
    assert token_env.value_from is not None
    assert token_env.value_from.secret_key_ref.name == f"jupyter-{name}"
    secret: Any = orch.core.read_namespaced_secret(f"jupyter-{name}", namespace)
    assert secret is not None
    sc = pod.spec.containers[0].security_context
    assert sc is not None
    assert sc.allow_privilege_escalation is False
    assert sc.capabilities is not None and sc.capabilities.drop == ["ALL"]
    assert set(sc.capabilities.add or []) == {"SYS_CHROOT", "SETUID", "SETGID"}
    assert sc.seccomp_profile is not None and sc.seccomp_profile.type == "RuntimeDefault"
    assert pod.spec.automount_service_account_token is False
    res = pod.spec.containers[0].resources
    assert res is not None
    assert res.limits is not None and res.limits.get("ephemeral-storage")
    assert res.requests is not None and res.requests.get("ephemeral-storage")

    pvc_name = instance_disk_pvc_name(name)
    orch.core.read_namespaced_persistent_volume_claim(pvc_name, namespace)
    svc: Any = orch.core.read_namespaced_service(name, namespace)
    assert svc.spec is not None and svc.spec.ports[0].node_port == 31999
    orch.core.read_namespaced_service(jupyter_service_name(name), namespace)
    route: Any = orch.custom.get_namespaced_custom_object(
        GATEWAY_API_GROUP, GATEWAY_API_VERSION, namespace, HTTPROUTE_PLURAL, name
    )
    parent = route["spec"]["parentRefs"][0]
    assert parent["name"] == GATEWAY_NAME and parent["sectionName"] == GATEWAY_APP_LISTENER

    await orch.delete_instance(namespace, name, force=True)
    await _wait_pod_gone(orch, namespace, name)
    orch.core.read_namespaced_persistent_volume_claim(pvc_name, namespace)
    with pytest.raises(k8s_client.ApiException) as exc_secret:
        orch.core.read_namespaced_secret(f"jupyter-{name}", namespace)
    assert exc_secret.value.status == 404

    await orch.delete_instance_disk(namespace, name)
    await _wait_pvc_gone(orch, namespace, pvc_name)
