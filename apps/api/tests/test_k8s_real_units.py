"""RealOrchestrator 的离线单测:纯函数与 stub 注入(sync 方法不连集群)。

真实集群行为由 test_k8s_real_smoke.py(SUPERDL_TEST_KUBECONFIG 门控)覆盖;
这里钉死「不需要集群就能验证」的逻辑:单位换算、端口区间、NetPol 结构、
分页、库存保守归账、Service 409 核对。
"""

from types import SimpleNamespace
from typing import Any, cast

import pytest
from kubernetes import client as k8s_client

from app.core.k8s.base import POOL_NODE_LABEL, InstancePodSpec, NodePortTaken
from app.core.k8s.real import (
    EGRESS_BLOCKED_TCP_PORTS,
    PRIVATE_CIDRS,
    TENANT_EPHEMERAL_LIMIT,
    RealOrchestrator,
)


def _bare() -> RealOrchestrator:
    """不跑 __init__(不加载 kubeconfig),只挂方法需要的属性。"""
    return RealOrchestrator.__new__(RealOrchestrator)


class TestQtyToBytes:
    """后缀匹配必须长后缀优先("Ki" 先于 "K"),不依赖 dict 插入顺序。"""

    @pytest.mark.parametrize(
        ("q", "expected"),
        [
            ("500Ki", 500 * 1024),
            ("500K", 500 * 1000),
            ("200Gi", 200 * 1024**3),
            ("200G", 200 * 1000**3),
            ("49192080Ki", 49192080 * 1024),
            ("1234", 1234),
            ("1.5Gi", int(1.5 * 1024**3)),
            (None, 0),
            ("", 0),
            ("garbage", 0),
            ("10Xi", 0),
        ],
    )
    def test_parse(self, q: str | None, expected: int):
        assert RealOrchestrator._qty_to_bytes(q) == expected


class TestEgressPortRanges:
    """TCP 允许区间必须恰好覆盖 1-65535 扣除黑名单,且 22(SSH 出)必须放行。"""

    def test_ranges(self):
        from app.core.k8s.real import _allowed_tcp_port_ranges

        covered: set[int] = set()
        for p in _allowed_tcp_port_ranges():
            assert p.protocol == "TCP"
            lo, hi = cast(int, p.port), cast(int, p.end_port or p.port)
            assert lo <= hi
            covered.update(range(lo, hi + 1))
        assert covered == set(range(1, 65536)) - set(EGRESS_BLOCKED_TCP_PORTS)
        for port in (22, 80, 443, 8080, 2379):  # SSH 出 / HTTP / HTTPS / 对象存储等常用
            assert port in covered

    def test_blocked_ports_excluded(self):
        from app.core.k8s.real import _allowed_tcp_port_ranges

        for p in _allowed_tcp_port_ranges():
            lo, hi = cast(int, p.port), cast(int, p.end_port or p.port)
            for blocked in EGRESS_BLOCKED_TCP_PORTS:
                assert not lo <= blocked <= hi


class TestTenantNetpol:
    """离线结构断言(与 kind 冒烟的对象级断言互补,模型字段一律按 Any 处理):"""

    def test_structure(self):
        policy = _bare()._tenant_netpol("tenant-x")
        spec: Any = policy.spec
        assert set(spec.policy_types) == {"Ingress", "Egress"}

        # 入方向:Jupyter(8888,仅 ingress-nginx)+ SSH(22,NodePort 显式放行)
        assert len(spec.ingress) == 2
        jupyter, ssh = spec.ingress
        assert [(p.protocol, p.port) for p in jupyter.ports] == [("TCP", 8888)]
        assert jupyter._from[0].pod_selector is None  # 不放行同 ns 其它 Pod
        assert [(p.protocol, p.port) for p in ssh.ports] == [("TCP", 22)]
        assert ssh._from[0].ip_block.cidr == "0.0.0.0/0"

        # 出方向:DNS(收敛 CoreDNS Pod)+ 公网 TCP(端口区间)+ 公网 UDP
        assert len(spec.egress) == 3
        dns = spec.egress[0].to[0]
        assert dns.namespace_selector.match_labels == {"kubernetes.io/metadata.name": "kube-system"}
        assert dns.pod_selector.match_labels == {"k8s-app": "kube-dns"}
        for rule in spec.egress[1:]:
            ip_block = rule.to[0].ip_block
            assert ip_block.cidr == "0.0.0.0/0"
            assert set(ip_block._except) == set(PRIVATE_CIDRS)

    def test_private_cidrs_cover_cgnat_and_metadata(self):
        # 云 metadata 169.254.169.254 必须被 169.254.0.0/16 覆盖;CGNAT 段有意封禁
        assert "169.254.0.0/16" in PRIVATE_CIDRS
        assert "100.64.0.0/10" in PRIVATE_CIDRS
        assert "198.18.0.0/15" in PRIVATE_CIDRS


def _page(items: list[Any], cont: str | None = None) -> Any:
    return SimpleNamespace(items=items, metadata=SimpleNamespace(_continue=cont))


class TestListAll:
    def test_pagination_collects_all_pages(self):
        calls: list[dict] = []

        def list_fn(**kwargs: Any) -> Any:
            calls.append(kwargs)
            if "_continue" not in kwargs:
                return _page(["a", "b"], cont="tok-1")
            return _page(["c"])

        out = RealOrchestrator._list_all(list_fn, label_selector="x=y")
        assert out == ["a", "b", "c"]
        assert calls[0]["limit"] == 500 and calls[0]["label_selector"] == "x=y"
        assert calls[1]["_continue"] == "tok-1"

    def test_single_page(self):
        out = RealOrchestrator._list_all(lambda **kw: _page([1]))
        assert out == [1]


def _pod(
    *,
    pool: str | None,
    node_name: str | None,
    gpu: int,
    cores: int | None = None,
) -> Any:
    limits = {"nvidia.com/gpu": str(gpu)}
    if cores is not None:
        limits["nvidia.com/gpucores"] = str(cores)
    return SimpleNamespace(
        spec=SimpleNamespace(
            node_selector=({POOL_NODE_LABEL: pool} if pool else None),
            node_name=node_name,
            containers=[SimpleNamespace(resources=SimpleNamespace(limits=limits))],
        )
    )


def _orch_with(pods: list[Any], nodes: list[Any]) -> RealOrchestrator:
    """挂 CoreStub 的裸 RealOrchestrator(不连集群)。"""
    orch = _bare()

    class CoreStub:
        def list_pod_for_all_namespaces(self, **kwargs: Any) -> Any:
            return _page(pods)

        def list_node(self, **kwargs: Any) -> Any:
            return _page(nodes)

    orch.core = cast(Any, CoreStub())
    return orch


class TestHamiCapacityAccounting:
    """HAMi 池容量口径:物理卡数取 GFD 标签,已用份额按 gpucores 折算(物理卡当量)。"""

    def _node(self, *, allocatable_gpu: int, gfd_count: str | None) -> Any:
        labels = {POOL_NODE_LABEL: "hami"}
        if gfd_count is not None:
            labels["nvidia.com/gpu.count"] = gfd_count
        return SimpleNamespace(
            metadata=SimpleNamespace(name="hami-n1", labels=labels),
            status=SimpleNamespace(allocatable={"nvidia.com/gpu": str(allocatable_gpu)}),
        )

    def test_physical_count_prefers_gfd_label(self):
        # 2 物理卡 × deviceSplitCount 10 → allocatable 20;物理口径必须是 2
        node = self._node(allocatable_gpu=20, gfd_count="2")
        assert RealOrchestrator._physical_gpu_amount(node) == 2

    def test_no_gfd_label_falls_back_to_allocatable(self):
        node = self._node(allocatable_gpu=8, gfd_count=None)
        assert RealOrchestrator._physical_gpu_amount(node) == 8

    def test_occupancy_by_gpucores(self):
        # 1 虚卡 × 50% 算力 = 0.5 物理卡当量
        assert (
            RealOrchestrator._pod_gpu_occupancy(
                {"nvidia.com/gpu": "1", "nvidia.com/gpucores": "50"}
            )
            == 0.5
        )
        # 2 虚卡 × 30% = 0.6;整卡无 gpucores = 1
        assert (
            RealOrchestrator._pod_gpu_occupancy(
                {"nvidia.com/gpu": "2", "nvidia.com/gpucores": "30"}
            )
            == 0.6
        )
        assert RealOrchestrator._pod_gpu_occupancy({"nvidia.com/gpu": "1"}) == 1.0
        assert RealOrchestrator._pod_gpu_occupancy({"nvidia.com/mig-1g.10gb": "2"}) == 2.0

    def test_used_pool_ceil_after_share_sum(self):
        nodes = [
            SimpleNamespace(metadata=SimpleNamespace(name="n1", labels={POOL_NODE_LABEL: "hami"}))
        ]
        pods = [
            _pod(pool="hami", node_name="n1", gpu=1, cores=50),
            _pod(pool="hami", node_name="n1", gpu=1, cores=30),
        ]
        # 0.5 + 0.3 = 0.8 → 向上取整 1(不低估占用)
        assert _orch_with(pods, nodes)._used_gpus_by_pool() == {"hami": 1}


class TestUsedGpusByPool:
    """无 nodeSelector 的 Pod 必须按 nodeName 所在节点保守归池,否则库存虚高超卖。"""

    def test_selectorless_pod_counted_by_node(self):
        nodes = [
            SimpleNamespace(metadata=SimpleNamespace(name="n1", labels={POOL_NODE_LABEL: "hami"}))
        ]
        pods = [
            _pod(pool="kata", node_name="n2", gpu=1),
            _pod(pool=None, node_name="n1", gpu=2),  # 无 selector:按节点归 hami
            _pod(pool=None, node_name=None, gpu=9),  # 未调度:无法归池,跳过
        ]
        assert _orch_with(pods, nodes)._used_gpus_by_pool() == {"kata": 1, "hami": 2}

    def test_all_selectorless_cluster_not_overcounted(self):
        nodes = [
            SimpleNamespace(metadata=SimpleNamespace(name="n1", labels={POOL_NODE_LABEL: "mig"}))
        ]
        pods = [_pod(pool=None, node_name="n1", gpu=1)]
        assert _orch_with(pods, nodes)._used_gpus_by_pool() == {"mig": 1}


def _api_exc(status: int, body: str = "") -> k8s_client.ApiException:
    exc = k8s_client.ApiException(status=status)
    exc.body = body
    return exc


def _spec(node_port: int = 31001) -> InstancePodSpec:
    return InstancePodSpec(
        namespace="tenant-1",
        name="inst-1",
        image="img:1",
        gpu_resources={},
        runtime_class=None,
        host_users=True,
        vcpu=1,
        mem_gb=1,
        disk_gb=10,
        ssh_node_port=node_port,
        jupyter_host="x.app.example.com",
    )


class TestServiceConflict:
    """SSH Service 409 不等于幂等成功:nodePort 必须读回核对,漂移则 patch。"""

    def _orch(self, existing: Any, create_exc: k8s_client.ApiException | None = None) -> Any:
        orch = _bare()
        calls: dict[str, list] = {"patch": []}

        class CoreStub:
            def create_namespaced_service(self, ns: str, svc: Any) -> None:
                raise create_exc or _api_exc(409)

            def read_namespaced_service(self, name: str, ns: str) -> Any:
                return existing

            def patch_namespaced_service(self, name: str, ns: str, body: Any) -> None:
                calls["patch"].append(body)

        orch.core = cast(Any, CoreStub())
        return orch, calls

    def _existing_svc(self, node_port: int | None, deleting: bool = False) -> Any:
        ports = [] if node_port is None else [SimpleNamespace(node_port=node_port)]
        return SimpleNamespace(
            metadata=SimpleNamespace(deletion_timestamp="ts" if deleting else None),
            spec=SimpleNamespace(ports=ports),
        )

    def test_same_node_port_is_idempotent_noop(self):
        orch, calls = self._orch(self._existing_svc(31001))
        orch._create_service_sync(_spec(31001))  # 不抛错
        assert calls["patch"] == []

    def test_drifted_node_port_is_patched(self):
        orch, calls = self._orch(self._existing_svc(31999))
        orch._create_service_sync(_spec(31001))
        assert len(calls["patch"]) == 1
        assert calls["patch"][0]["spec"]["ports"][0]["nodePort"] == 31001

    def test_terminating_service_raises(self):
        orch, _calls = self._orch(self._existing_svc(31001, deleting=True))
        with pytest.raises(RuntimeError, match="terminating"):
            orch._create_service_sync(_spec(31001))

    def test_port_taken_on_create_raises_nodeporttaken(self):
        orch, _calls = self._orch(
            self._existing_svc(31001), _api_exc(422, "provided port is already allocated")
        )
        with pytest.raises(NodePortTaken):
            orch._create_service_sync(_spec(31001))

    def test_port_taken_on_patch_raises_nodeporttaken(self):
        orch, _calls = self._orch(self._existing_svc(31999))

        def _patch(name: str, ns: str, body: Any) -> None:
            raise _api_exc(422, "provided port is already allocated")

        orch.core.patch_namespaced_service = _patch
        with pytest.raises(NodePortTaken):
            orch._create_service_sync(_spec(31001))


class TestTenantQuota:
    """含 cpu/memory/ephemeral 的 Quota 是兜底;patch 收敛覆盖存量 ns。"""

    def test_conflict_patches_existing(self):
        orch = _bare()
        calls: list[Any] = []

        class CoreStub:
            def create_namespaced_resource_quota(self, ns: str, quota: Any) -> None:
                raise _api_exc(409)

            def patch_namespaced_resource_quota(self, name: str, ns: str, quota: Any) -> None:
                calls.append(quota)

        orch.core = cast(Any, CoreStub())
        orch._ensure_quota_sync("tenant-1")
        assert len(calls) == 1
        hard = calls[0].spec.hard
        assert "requests.cpu" in hard and "limits.ephemeral-storage" in hard

    def test_ephemeral_limit_generous(self):
        """限额必须远高于镜像常规可写用量(超限=驱逐 Pod,不能误伤正常租户)。"""
        assert RealOrchestrator._qty_to_bytes(TENANT_EPHEMERAL_LIMIT) >= 32 * 1024**3
