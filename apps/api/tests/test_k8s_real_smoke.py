"""真实 K8s 编排冒烟(默认跳过,CI 由 kind job 驱动)。

门控:环境变量 SUPERDL_TEST_KUBECONFIG 指向可用 kubeconfig(CI 的 kind 集群)。
未设置时整文件 skip,不影响本地默认 `uv run pytest`(conftest 强制 fake 后端
只影响 get_orchestrator,这里直接构造 RealOrchestrator,互不干扰)。

覆盖单测(fake 后端)够不着的两条安全路径:
- 租户 namespace 的默认 NetworkPolicy:东西向默认拒,仅放行 ingress-nginx → Jupyter 8888;
  出方向白名单公网 + DNS,禁访私网/云元数据网段
- 实例盘生命周期:删除实例不动盘,仅显式 delete_instance_disk(释放/回收)才删盘

kind 默认 CNI(kindnet)不执行 NetworkPolicy,隔离断言落在对象规约(apiserver 落库的内容)
而非实际流量;功能性流量隔离由集群交付的 Cilium 保证,不在此冒烟范围。
JuiceFS/TopoLVM 在 kind 不存在,PVC 停留 Pending 属预期 —— 冒烟只验证对象生命周期。
"""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from kubernetes import client as k8s_client

from app.core.k8s.base import (
    JUICEFS_PVC_NAME,
    InstancePodSpec,
    instance_disk_pvc_name,
    jupyter_service_name,
)
from app.core.k8s.real import (
    EGRESS_BLOCKED_TCP_PORTS,
    INGRESS_NAMESPACE,
    MANAGED_LABEL,
    PRIVATE_CIDRS,
    RealOrchestrator,
)

pytestmark = [
    pytest.mark.real_k8s,
    pytest.mark.skipif(
        not os.environ.get("SUPERDL_TEST_KUBECONFIG"),
        reason="SUPERDL_TEST_KUBECONFIG 未设置,跳过真实 K8s 冒烟",
    ),
]

POD_GONE_TIMEOUT = 30.0  # force 删除通常秒级,留足余量防 CI 抖动


@pytest.fixture(scope="module")
def orch() -> RealOrchestrator:
    """直连集群的 RealOrchestrator;SUPERDL_TEST_KUBECONFIG 转标准 KUBECONFIG 供客户端读取。"""
    os.environ["KUBECONFIG"] = os.environ["SUPERDL_TEST_KUBECONFIG"]
    return RealOrchestrator()


@pytest.fixture
async def namespace(orch: RealOrchestrator) -> AsyncIterator[str]:
    """每用例一只独立租户 ns(带前缀,与 list_instance_pods 的口径一致),结束即删。"""
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


async def test_namespace_security_baseline(orch: RealOrchestrator, namespace: str) -> None:
    """租户 ns 引导件:PSA 标签 + 默认 NetPol 隔离 + 配额 + 共享数据盘 PVC;重复下发幂等。"""
    await orch.ensure_namespace(namespace)  # 幂等:fixture 已建,重复下发不得报错

    # 官方客户端方法为动态生成,pyright 推不出返回类型,断言侧一律 Any(同 real.py 惯例)
    ns: Any = orch.core.read_namespace(namespace)
    assert ns.metadata.labels[MANAGED_LABEL] == "true"
    # PSA:enforce 只到 baseline(平台镜像以 root 运行,restricted 会拒绝全部租户 Pod);
    # audit/warn 打 restricted 留审计轨迹
    assert ns.metadata.labels["pod-security.kubernetes.io/enforce"] == "baseline"
    assert ns.metadata.labels["pod-security.kubernetes.io/audit"] == "restricted"

    netpol: Any = orch.net.read_namespaced_network_policy("tenant-default", namespace)
    spec = netpol.spec
    assert spec is not None and set(spec.policy_types) == {"Ingress", "Egress"}

    # 入方向:ingress-nginx → Jupyter 8888(北向)+ 0.0.0.0/0 → SSH 22(NodePort 显式放行,
    # 不依赖「NodePort 不过 NetworkPolicy」的 CNI 隐式行为),其余东西向默认拒
    assert spec.ingress is not None and len(spec.ingress) == 2
    jupyter_rule, ssh_rule = spec.ingress
    assert jupyter_rule.ports is not None
    assert [(p.protocol, p.port) for p in jupyter_rule.ports] == [("TCP", 8888)]
    assert jupyter_rule._from is not None and len(jupyter_rule._from) == 1
    peer = jupyter_rule._from[0]
    assert peer.namespace_selector is not None
    assert peer.namespace_selector.match_labels == {
        "kubernetes.io/metadata.name": INGRESS_NAMESPACE
    }
    assert ssh_rule.ports is not None
    assert [(p.protocol, p.port) for p in ssh_rule.ports] == [("TCP", 22)]
    assert ssh_rule._from is not None and ssh_rule._from[0].ip_block is not None
    assert ssh_rule._from[0].ip_block.cidr == "0.0.0.0/0"

    # 出方向:DNS(收敛到 CoreDNS Pod)+ 公网 TCP(端口黑名单)+ 公网 UDP,其余默认拒
    assert spec.egress is not None and len(spec.egress) == 3
    dns_rule = spec.egress[0]
    assert dns_rule.to is not None and len(dns_rule.to) == 1
    dns_peer = dns_rule.to[0]
    assert dns_peer.namespace_selector is not None
    assert dns_peer.namespace_selector.match_labels == {
        "kubernetes.io/metadata.name": "kube-system"
    }
    assert dns_peer.pod_selector is not None
    assert dns_peer.pod_selector.match_labels == {"k8s-app": "kube-dns"}
    assert dns_rule.ports is not None
    assert {(p.protocol, p.port) for p in dns_rule.ports} == {("UDP", 53), ("TCP", 53)}

    tcp_rule, udp_rule = spec.egress[1], spec.egress[2]
    for rule in (tcp_rule, udp_rule):
        assert rule.to is not None and rule.to[0].ip_block is not None
        assert rule.to[0].ip_block.cidr == "0.0.0.0/0"
        assert set(rule.to[0].ip_block._except or []) == set(PRIVATE_CIDRS)
    # TCP 端口区间必须恰好覆盖 1-65535 扣除黑名单
    assert tcp_rule.ports is not None
    blocked = set(EGRESS_BLOCKED_TCP_PORTS)
    covered: set[int] = set()
    for p in tcp_rule.ports:
        assert p.protocol == "TCP"
        lo, hi = int(p.port), int(p.end_port or p.port)
        covered.update(range(lo, hi + 1))
    assert covered == set(range(1, 65536)) - blocked
    assert udp_rule.ports is not None
    assert [(p.protocol, p.port) for p in udp_rule.ports] == [("UDP", None)]

    # 配额兜底(对象数 + 资源总量)与共享数据盘 PVC 就位(Pending 即可,kind 无对应 SC)
    quota: Any = orch.core.read_namespaced_resource_quota("tenant-quota", namespace)
    assert quota.spec is not None and "pods" in quota.spec.hard
    assert "requests.cpu" in quota.spec.hard and "limits.ephemeral-storage" in quota.spec.hard
    orch.core.read_namespaced_persistent_volume_claim(JUICEFS_PVC_NAME, namespace)


async def test_instance_lifecycle_and_disk_reclaim(orch: RealOrchestrator, namespace: str) -> None:
    """实例全生命周期:创建(幂等)→ 加固断言 → 删实例留盘 → 显式回收盘。"""
    name = f"smoke-{uuid.uuid4().hex[:12]}"
    spec = InstancePodSpec(
        namespace=namespace,
        name=name,
        # 故意不可拉取的镜像:Pod 停 Pending,冒烟不依赖外网镜像仓库
        image="registry.invalid/smoke:0",
        gpu_resources={},
        runtime_class=None,
        host_users=True,
        vcpu=1,
        mem_gb=1,
        disk_gb=1,
        ssh_node_port=31999,
        jupyter_host=f"{name}.app.example.invalid",
        env={"JUPYTER_TOKEN": "smoke"},
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
    # 租户容器加固基线必须无条件下发(real.py tenant_security_context)
    sc = pod.spec.containers[0].security_context
    assert sc is not None
    assert sc.allow_privilege_escalation is False
    assert sc.capabilities is not None and sc.capabilities.drop == ["ALL"]
    assert sc.seccomp_profile is not None and sc.seccomp_profile.type == "RuntimeDefault"
    assert pod.spec.automount_service_account_token is False
    # ephemeral-storage 限额(可写层+日志):防写爆节点盘连坐整节点
    res = pod.spec.containers[0].resources
    assert res is not None
    assert res.limits is not None and res.limits.get("ephemeral-storage")
    assert res.requests is not None and res.requests.get("ephemeral-storage")

    pvc_name = instance_disk_pvc_name(name)
    orch.core.read_namespaced_persistent_volume_claim(pvc_name, namespace)  # 盘已建
    svc: Any = orch.core.read_namespaced_service(name, namespace)
    assert svc.spec is not None and svc.spec.ports[0].node_port == 31999
    orch.core.read_namespaced_service(jupyter_service_name(name), namespace)
    orch.net.read_namespaced_ingress(name, namespace)

    # 删实例:Pod/SVC/Ingress 清除,实例盘必须保留(数据活过关机,见 base.py 契约)
    await orch.delete_instance(namespace, name, force=True)
    await _wait_pod_gone(orch, namespace, name)
    orch.core.read_namespaced_persistent_volume_claim(pvc_name, namespace)  # 盘还在

    # 显式回收(释放/回收路径唯一允许的删盘入口):盘删除成功
    await orch.delete_instance_disk(namespace, name)
    with pytest.raises(k8s_client.ApiException) as exc_info:
        orch.core.read_namespaced_persistent_volume_claim(pvc_name, namespace)
    assert exc_info.value.status == 404
