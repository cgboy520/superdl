"""真实 K8s 编排冒烟(默认跳过,CI 由 kind job 驱动)。

门控:环境变量 SUPERDL_TEST_KUBECONFIG 指向可用 kubeconfig(CI 的 kind 集群)。
未设置时整文件 skip,不影响本地默认 `uv run pytest`(conftest 强制 fake 后端
只影响 get_orchestrator,这里直接构造 RealOrchestrator,互不干扰)。

覆盖单测(fake 后端)够不着的两条安全路径:
- 租户 namespace 的引导件经 apiserver 落库(PSA 标签、NetworkPolicy、配额、共享 PVC);
  NetPol 的结构在 test_k8s_real_units 离线钉死,这里只验 apiserver 接受 endPort/except
- 实例盘生命周期:删除实例不动盘,仅显式 delete_instance_disk(释放/回收)才删盘

kind 默认 CNI(kindnet)不执行 NetworkPolicy,断言落在对象规约而非实际流量;
功能性流量隔离由集群交付的 Cilium 保证,不在此冒烟范围。
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
from app.core.k8s.real import MANAGED_LABEL, PRIVATE_CIDRS, RealOrchestrator

pytestmark = [
    pytest.mark.real_k8s,
    pytest.mark.skipif(
        not os.environ.get("SUPERDL_TEST_KUBECONFIG"),
        reason="SUPERDL_TEST_KUBECONFIG 未设置,跳过真实 K8s 冒烟",
    ),
]

POD_GONE_TIMEOUT = 30.0  # force 删除通常秒级,留足余量防 CI 抖动
# PVC 删除是异步的(pvc-protection finalizer 清掉才真消失),不能删完立刻断言 404
PVC_GONE_TIMEOUT = 30.0


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


async def _wait_pvc_gone(orch: RealOrchestrator, namespace: str, pvc_name: str) -> None:
    """删盘同样等对象真消失:apiserver 先打 deletionTimestamp,finalizer 清完才 404。"""
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

    # 官方客户端方法为动态生成,pyright 推不出返回类型,断言侧一律 Any(同 real.py 惯例)
    ns: Any = orch.core.read_namespace(namespace)
    assert ns.metadata.labels[MANAGED_LABEL] == "true"
    # PSA:enforce 只到 baseline(平台镜像以 root 运行,restricted 会拒绝全部租户 Pod);
    # audit/warn 打 restricted 留审计轨迹
    assert ns.metadata.labels["pod-security.kubernetes.io/enforce"] == "baseline"
    assert ns.metadata.labels["pod-security.kubernetes.io/audit"] == "restricted"

    # NetPol 经 apiserver 落库:只验集群接受 endPort 区间与 ipBlock.except(旧版本 / 部分
    # CNI 会拒收或丢弃这两项),规约结构由 test_k8s_real_units 离线钉死
    netpol: Any = orch.net.read_namespaced_network_policy("tenant-default", namespace)
    spec = netpol.spec
    assert spec is not None and set(spec.policy_types) == {"Ingress", "Egress"}
    assert spec.egress is not None and len(spec.egress) == 3
    tcp_rule = spec.egress[1]
    assert tcp_rule.ports is not None and any(p.end_port for p in tcp_rule.ports)
    assert tcp_rule.to is not None and tcp_rule.to[0].ip_block is not None
    assert set(tcp_rule.to[0].ip_block._except or []) == set(PRIVATE_CIDRS)

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
    # JUPYTER_TOKEN 必须以 secretKeyRef 引用 per-instance Secret,明文不进 Pod spec
    # (spec 进 etcd/审计快照,任何 pods:get/list 身份都能读)
    token_env = next(e for e in pod.spec.containers[0].env if e.name == "JUPYTER_TOKEN")
    assert token_env.value is None
    assert token_env.value_from is not None
    assert token_env.value_from.secret_key_ref.name == f"jupyter-{name}"
    secret: Any = orch.core.read_namespaced_secret(f"jupyter-{name}", namespace)
    assert secret is not None
    # 租户容器加固基线必须无条件下发(real.py tenant_security_context)
    sc = pod.spec.containers[0].security_context
    assert sc is not None
    assert sc.allow_privilege_escalation is False
    assert sc.capabilities is not None and sc.capabilities.drop == ["ALL"]
    # 这三个是 SSH 的硬前置(OpenSSH 预认证特权分离要 chroot + setgid/setuid),
    # 少任何一个,平台承诺的 ssh root@ 入口在密钥交换阶段就断;多给别的则是加固回退
    assert set(sc.capabilities.add or []) == {"SYS_CHROOT", "SETUID", "SETGID"}
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

    # 删实例:Pod/SVC/Ingress/Secret 清除,实例盘必须保留(数据活过关机,见 base.py 契约)
    await orch.delete_instance(namespace, name, force=True)
    await _wait_pod_gone(orch, namespace, name)
    orch.core.read_namespaced_persistent_volume_claim(pvc_name, namespace)  # 盘还在
    # token Secret 随实例一并摘除(不留凭据残骸)
    with pytest.raises(k8s_client.ApiException) as exc_secret:
        orch.core.read_namespaced_secret(f"jupyter-{name}", namespace)
    assert exc_secret.value.status == 404

    # 显式回收(释放/回收路径唯一允许的删盘入口):盘删除成功
    await orch.delete_instance_disk(namespace, name)
    await _wait_pvc_gone(orch, namespace, pvc_name)
