"""真实 K8s 编排冒烟:SUPERDL_TEST_KUBECONFIG 未设置时整文件 skip(CI 由 kind job 驱动)。
覆盖租户 ns 引导件经 apiserver 落库、实例盘生命周期;只验对象规约,PVC Pending 属预期。"""

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
from tests.helpers import use_kubeconfig

pytestmark = [
    pytest.mark.real_k8s,
    pytest.mark.skipif(
        not os.environ.get("SUPERDL_TEST_KUBECONFIG"),
        reason="SUPERDL_TEST_KUBECONFIG 未设置,跳过真实 K8s 冒烟",
    ),
]

POD_GONE_TIMEOUT = 30.0  # force 删除通常秒级,留足余量防 CI 抖动
# PVC 删除异步(pvc-protection finalizer)
PVC_GONE_TIMEOUT = 30.0


@pytest.fixture(scope="module")
def orch() -> RealOrchestrator:
    """直连集群的 RealOrchestrator;SUPERDL_TEST_KUBECONFIG 转标准 KUBECONFIG 供客户端读取。"""
    use_kubeconfig(os.environ["SUPERDL_TEST_KUBECONFIG"])
    return RealOrchestrator()


@pytest.fixture(scope="module")
def orch_restricted() -> RealOrchestrator:
    """受限身份(superdl-tenant-mgr SA)的编排器:SUPERDL_TEST_KUBECONFIG_RESTRICTED 缺失即 skip。"""
    path = os.environ.get("SUPERDL_TEST_KUBECONFIG_RESTRICTED")
    if not path:
        pytest.skip("SUPERDL_TEST_KUBECONFIG_RESTRICTED 未设置(仅 CI kind job 注入受限身份)")
    use_kubeconfig(path)
    return RealOrchestrator()


@pytest.fixture
async def namespace(orch: RealOrchestrator) -> AsyncIterator[str]:
    """每用例一只独立租户 ns,结束即删。"""
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
            raise TimeoutError(f"pod {name} 删除超时")
        await asyncio.sleep(0.5)


async def _wait_pvc_gone(orch: RealOrchestrator, namespace: str, pvc_name: str) -> None:
    """等 PVC 真消失(finalizer 清完才 404)。"""
    deadline = asyncio.get_running_loop().time() + PVC_GONE_TIMEOUT
    while True:
        try:
            orch.core.read_namespaced_persistent_volume_claim(pvc_name, namespace)
        except k8s_client.ApiException as exc:
            if exc.status == 404:
                return
            raise
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError(f"pvc {pvc_name} 删除超时")
        await asyncio.sleep(0.5)


async def test_namespace_security_baseline(orch: RealOrchestrator, namespace: str) -> None:
    """租户 ns 引导件:PSA 标签 + 默认 NetPol 隔离 + 配额 + 共享数据盘 PVC;重复下发幂等。"""
    await orch.ensure_namespace(namespace)  # 幂等:fixture 已建,重复下发不得报错

    # 官方客户端返回类型推不出,断言侧一律 Any
    ns: Any = orch.core.read_namespace(namespace)
    assert ns.metadata.labels[MANAGED_LABEL] == "true"
    # PSA:enforce=baseline,audit/warn=restricted
    assert ns.metadata.labels["pod-security.kubernetes.io/enforce"] == "baseline"
    assert ns.metadata.labels["pod-security.kubernetes.io/audit"] == "restricted"

    # NetPol:只验 apiserver 接受 endPort 与 ipBlock.except,结构由 test_k8s_real_units 钉
    netpol: Any = orch.net.read_namespaced_network_policy("tenant-default", namespace)
    spec = netpol.spec
    assert spec is not None and set(spec.policy_types) == {"Ingress", "Egress"}
    assert spec.egress is not None and len(spec.egress) == 3
    tcp_rule = spec.egress[1]
    assert tcp_rule.ports is not None and any(p.end_port for p in tcp_rule.ports)
    assert tcp_rule.to is not None and tcp_rule.to[0].ip_block is not None
    assert set(tcp_rule.to[0].ip_block._except or []) == set(PRIVATE_CIDRS)
    # SSH(22)入方向排掉 Pod 网段
    assert spec.ingress is not None and len(spec.ingress) == 2
    ssh_block = spec.ingress[1]._from[0].ip_block
    assert ssh_block is not None and ssh_block.cidr == "0.0.0.0/0"
    assert ssh_block._except == [get_settings().tenant_pod_cidr]

    # 配额(对象数 + 资源总量)就位;数据盘 PVC 不在建 ns 时创建,一盘一只按需建
    quota: Any = orch.core.read_namespaced_resource_quota("tenant-quota", namespace)
    assert quota.spec is not None and "pods" in quota.spec.hard
    assert "requests.cpu" in quota.spec.hard and "limits.ephemeral-storage" in quota.spec.hard

    # 数据盘 PVC 往返:建 → 幂等重入 → 扩容 → 删;挂了 = 一盘一 PVC 的下发面漂了
    pvc_name = data_disk_pvc_name("a" * 32)
    await orch.ensure_data_disk(namespace, pvc_name, 10)
    created: Any = orch.core.read_namespaced_persistent_volume_claim(pvc_name, namespace)
    assert created.spec.resources.requests["storage"] == "10Gi"
    assert created.spec.access_modes == ["ReadWriteMany"]
    await orch.ensure_data_disk(namespace, pvc_name, 10)  # 幂等
    await orch.ensure_data_disk(namespace, pvc_name, 20)  # 扩容
    grown: Any = orch.core.read_namespaced_persistent_volume_claim(pvc_name, namespace)
    assert grown.spec.resources.requests["storage"] == "20Gi"
    await orch.ensure_data_disk(namespace, pvc_name, 5)  # 缩容不动
    same: Any = orch.core.read_namespaced_persistent_volume_claim(pvc_name, namespace)
    assert same.spec.resources.requests["storage"] == "20Gi"
    await orch.delete_data_disk(namespace, pvc_name)
    await orch.delete_data_disk(namespace, pvc_name)  # 删两遍不报错


async def test_ensure_namespace_under_tenant_mgr_sa(orch_restricted: RealOrchestrator) -> None:
    """RBAC 对齐闸:用 superdl-tenant-mgr 受限身份跑 ensure_namespace 两遍(含 409→patch);
    挂了 = 01-rbac.yaml 与 real.py 的 _ensure_* 漂移。"""
    from kubernetes import config as k8s_config

    ns = f"tenant-rbac-{uuid.uuid4().hex[:8]}"
    await orch_restricted.ensure_namespace(ns)
    try:
        # 第二遍:全部对象已存在,走 patch 收敛
        await orch_restricted.ensure_namespace(ns)
        ns_obj: Any = orch_restricted.core.read_namespace(ns)
        assert ns_obj.metadata.labels[MANAGED_LABEL] == "true"
        orch_restricted.core.read_namespaced_resource_quota("tenant-quota", ns)
        orch_restricted.core.read_namespaced_limit_range("tenant-defaults", ns)
        orch_restricted.net.read_namespaced_network_policy("tenant-default", ns)
    finally:
        # ns 删除超出租户 SA 权限面,用管理面 kubeconfig 清理
        k8s_config.load_kube_config(config_file=os.environ["SUPERDL_TEST_KUBECONFIG"])
        await asyncio.to_thread(k8s_client.CoreV1Api().delete_namespace, ns)


async def test_instance_lifecycle_and_disk_reclaim(orch: RealOrchestrator, namespace: str) -> None:
    """实例全生命周期:创建(幂等)→ 加固断言 → 删实例留盘 → 显式回收盘。"""
    name = f"smoke-{uuid.uuid4().hex[:12]}"
    spec = InstancePodSpec(
        namespace=namespace,
        name=name,
        # 不可拉取的镜像:Pod 停 Pending
        image="registry.invalid/smoke:0",
        gpu_resources={},
        runtime_class=None,
        host_users=True,
        vcpu=1,
        mem_gb=1,
        disk_gb=1,
        ssh_node_port=31999,
        jupyter_host=f"{name}.app.example.invalid",
        secret_env={"JUPYTER_TOKEN": "smoke"},  # token 走 per-instance Secret,不落 Pod spec
        authorized_keys=(
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeFakeFakeFakeFakeFakeFakeFakeFak smoke",
        ),
    )
    await orch.create_instance(spec)
    await orch.create_instance(spec)  # 幂等重放(outbox at-least-once 语义)

    status = await orch.get_status(namespace, name)
    assert status.exists and not status.ready and not status.deleting

    pod: Any = orch.core.read_namespaced_pod(name, namespace)
    assert pod.spec is not None
    # JUPYTER_TOKEN 以 secretKeyRef 引用 per-instance Secret,明文不进 Pod spec
    token_env = next(e for e in pod.spec.containers[0].env if e.name == "JUPYTER_TOKEN")
    assert token_env.value is None
    assert token_env.value_from is not None
    assert token_env.value_from.secret_key_ref.name == f"jupyter-{name}"
    secret: Any = orch.core.read_namespaced_secret(f"jupyter-{name}", namespace)
    assert secret is not None
    # 租户容器加固基线(real.py tenant_security_context)
    sc = pod.spec.containers[0].security_context
    assert sc is not None
    assert sc.allow_privilege_escalation is False
    assert sc.capabilities is not None and sc.capabilities.drop == ["ALL"]
    # SSH 硬前置的三个 capability,不多不少
    assert set(sc.capabilities.add or []) == {"SYS_CHROOT", "SETUID", "SETGID"}
    assert sc.seccomp_profile is not None and sc.seccomp_profile.type == "RuntimeDefault"
    assert pod.spec.automount_service_account_token is False
    # ephemeral-storage 限额
    res = pod.spec.containers[0].resources
    assert res is not None
    assert res.limits is not None and res.limits.get("ephemeral-storage")
    assert res.requests is not None and res.requests.get("ephemeral-storage")

    pvc_name = instance_disk_pvc_name(name)
    orch.core.read_namespaced_persistent_volume_claim(pvc_name, namespace)  # 盘已建
    svc: Any = orch.core.read_namespaced_service(name, namespace)
    assert svc.spec is not None and svc.spec.ports[0].node_port == 31999
    orch.core.read_namespaced_service(jupyter_service_name(name), namespace)
    # apiserver 接受 parentRefs/hostnames/backendRefs 形状
    route: Any = orch.custom.get_namespaced_custom_object(
        GATEWAY_API_GROUP, GATEWAY_API_VERSION, namespace, HTTPROUTE_PLURAL, name
    )
    parent = route["spec"]["parentRefs"][0]
    assert parent["name"] == GATEWAY_NAME and parent["sectionName"] == GATEWAY_APP_LISTENER

    # 删实例:Pod/SVC/HTTPRoute/Secret 清除,实例盘保留(base.py 契约)
    await orch.delete_instance(namespace, name, force=True)
    await _wait_pod_gone(orch, namespace, name)
    orch.core.read_namespaced_persistent_volume_claim(pvc_name, namespace)  # 盘还在
    # token Secret 随实例摘除
    with pytest.raises(k8s_client.ApiException) as exc_secret:
        orch.core.read_namespaced_secret(f"jupyter-{name}", namespace)
    assert exc_secret.value.status == 404

    # 显式回收:盘删除成功
    await orch.delete_instance_disk(namespace, name)
    await _wait_pvc_gone(orch, namespace, pvc_name)
