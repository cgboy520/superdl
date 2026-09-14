"""RealOrchestrator 离线单测(不连集群):单位换算、端口区间、NetPol 结构、分页、
节点容量与份额归账、Service 409/422 核对。真实集群见 test_k8s_real_smoke.py。"""

# 白盒用例:直探模块内部
# pyright: reportPrivateUsage=false

from types import SimpleNamespace
from typing import Any, cast

import pytest
from kubernetes import client as k8s_client

from app.core.k8s.base import POOL_NODE_LABEL, InstancePodSpec, NodePortTaken
from app.core.k8s.real import (
    EGRESS_BLOCKED_TCP_PORTS,
    GATEWAY_DATAPLANE_NAMESPACE,
    PRIVATE_CIDRS,
    RealOrchestrator,
    pod_cidr_gateways,
)


def _bare() -> RealOrchestrator:
    """不跑 __init__,只挂方法需要的属性。"""
    return RealOrchestrator.__new__(RealOrchestrator)


class TestQtyToBytes:
    """长后缀优先("Ki" 先于 "K")、十进制单位、小数量、无法解析归 0。"""

    @pytest.mark.parametrize(
        ("q", "expected"),
        [
            ("500Ki", 500 * 1024),
            ("200G", 200 * 1000**3),
            ("1.5Gi", int(1.5 * 1024**3)),
            ("garbage", 0),
        ],
    )
    def test_parse(self, q: str | None, expected: int):
        assert RealOrchestrator._qty_to_bytes(q) == expected


class TestEgressPortRanges:
    """TCP 允许区间恰好覆盖 1-65535 扣除黑名单。"""

    def test_ranges(self):
        from app.core.k8s.real import _allowed_tcp_port_ranges

        covered: set[int] = set()
        for p in _allowed_tcp_port_ranges():
            assert p.protocol == "TCP"
            lo, hi = cast(int, p.port), cast(int, p.end_port or p.port)
            assert lo <= hi
            covered.update(range(lo, hi + 1))
        assert covered == set(range(1, 65536)) - set(EGRESS_BLOCKED_TCP_PORTS)


class TestTenantNetpol:
    """NetworkPolicy 离线结构断言(模型字段按 Any 处理)。"""

    @staticmethod
    def _orch(pod_cidr: str = "10.42.0.0/16") -> RealOrchestrator:
        orch = _bare()
        orch.settings = cast(Any, SimpleNamespace(tenant_pod_cidr=pod_cidr))
        return orch

    def test_structure(self):
        policy = self._orch()._tenant_netpol("tenant-x")
        spec: Any = policy.spec
        assert set(spec.policy_types) == {"Ingress", "Egress"}

        # 入方向:网关数据面 ns(不限端口)+ SSH(22,NodePort 显式放行)
        assert len(spec.ingress) == 2
        gateway, ssh = spec.ingress
        # 网关入方向不限端口(容器端口由用户声明)
        assert gateway.ports is None
        assert gateway._from[0].pod_selector is None  # 不放行同 ns 其它 Pod
        # 放行来源 = Envoy 数据面 ns(与 deploy/cluster/helmfile 的 release namespace 同源)
        assert gateway._from[0].namespace_selector.match_labels == {
            "kubernetes.io/metadata.name": GATEWAY_DATAPLANE_NAMESPACE
        }
        assert [(p.protocol, p.port) for p in ssh.ports] == [("TCP", 22)]
        assert ssh._from[0].ip_block.cidr == "0.0.0.0/0"
        # 排 Pod 网段,不排整个私网
        assert ssh._from[0].ip_block._except == ["10.42.0.0/16"]

        # 出方向:DNS(收敛 CoreDNS Pod)+ 公网 TCP(端口区间)+ 公网 UDP(白名单 53/443)
        assert len(spec.egress) == 3
        dns = spec.egress[0].to[0]
        assert dns.namespace_selector.match_labels == {"kubernetes.io/metadata.name": "kube-system"}
        assert dns.pod_selector.match_labels == {"k8s-app": "kube-dns"}
        for rule in spec.egress[1:]:
            ip_block = rule.to[0].ip_block
            assert ip_block.cidr == "0.0.0.0/0"
            assert set(ip_block._except) == set(PRIVATE_CIDRS)
        udp_ports = spec.egress[2].ports
        assert {(p.protocol, p.port) for p in udp_ports} == {("UDP", 53), ("UDP", 443)}

    def test_ssh_ingress_except_is_pod_cidr_only(self):
        """只排 Pod 网段,不排整个私网。"""
        spec: Any = self._orch("10.244.0.0/16")._tenant_netpol("tenant-x").spec
        ssh = spec.ingress[1]
        ip_block: Any = ssh._from[0].ip_block
        assert ip_block._except == ["10.244.0.0/16"]
        assert not set(PRIVATE_CIDRS) <= set(ip_block._except)

    def test_ssh_ingress_allows_node_pod_subnet_gateways(self):
        """逐节点放回 Pod 子网的 .0/32 与 .1/32(flannel 跨节点 NodePort 的 SNAT 来源)。"""
        gws = pod_cidr_gateways(["10.42.5.0/24", "10.42.0.0/24", "fd00::/64", "not-a-cidr"])
        assert gws == ["10.42.0.0/32", "10.42.0.1/32", "10.42.5.0/32", "10.42.5.1/32"]
        spec: Any = self._orch()._tenant_netpol("tenant-x", gws).spec
        peers = spec.ingress[1]._from
        assert peers[0].ip_block._except == ["10.42.0.0/16"]
        assert [p.ip_block.cidr for p in peers[1:]] == gws
        assert all(p.ip_block._except is None for p in peers[1:])

    def test_no_gateway_peers_when_pod_cidr_not_excluded(self):
        """不排 Pod 网段时不下发网关 /32。"""
        spec: Any = self._orch("")._tenant_netpol("tenant-x", ["10.42.0.0/32"]).spec
        assert len(spec.ingress[1]._from) == 1

    def test_empty_pod_cidr_emits_no_except(self):
        """留空时 except 字段整个缺席,不是 []。"""
        spec: Any = self._orch("")._tenant_netpol("tenant-x").spec
        ssh = spec.ingress[1]
        assert ssh._from[0].ip_block._except is None


class TestInstanceSecretHandling:
    """JUPYTER_TOKEN 走 per-instance Secret + secretKeyRef,明文不落 Pod spec。"""

    def _spec(self) -> InstancePodSpec:
        return InstancePodSpec(
            namespace="tenant-1",
            name="inst-1",
            image="img",
            gpu_resources={},
            runtime_class=None,
            host_users=True,
            vcpu=1,
            mem_gb=1,
            disk_gb=1,
            ssh_node_port=31234,
            jupyter_host="inst-1.app.example.com",
            secret_env={"JUPYTER_TOKEN": "plain-token-1"},
        )

    def test_secret_created_and_pod_references_it(self):
        orch = _bare()
        created: dict[str, Any] = {}

        class Core:
            def create_namespaced_secret(self, ns: str, secret: Any) -> None:
                created["secret"] = secret

            def create_namespaced_pod(self, ns: str, pod: Any) -> None:
                created["pod"] = pod

        orch.core = cast(Any, Core())
        spec = self._spec()
        orch._ensure_instance_secret_sync(spec)
        assert created["secret"].metadata.name == "jupyter-inst-1"
        assert created["secret"].string_data == {"JUPYTER_TOKEN": "plain-token-1"}
        assert created["secret"].metadata.labels["superdl.io/managed"] == "true"

        orch._create_pod_sync(spec)
        env = created["pod"].spec.containers[0].env
        token_env = next(e for e in env if e.name == "JUPYTER_TOKEN")
        assert token_env.value is None  # 明文不落 spec
        assert token_env.value_from.secret_key_ref.name == "jupyter-inst-1"
        assert token_env.value_from.secret_key_ref.key == "JUPYTER_TOKEN"

    def test_existing_secret_is_patched_not_duplicated(self):
        """幂等重放:已存在则 patch 收敛。"""
        orch = _bare()
        calls: list[str] = []
        body_holder: dict[str, Any] = {}

        class Core:
            def create_namespaced_secret(self, ns: str, secret: Any) -> None:
                calls.append("create")
                raise k8s_client.ApiException(status=409)

            def patch_namespaced_secret(self, name: str, ns: str, body: Any) -> None:
                calls.append("patch")
                body_holder.update(body)

        orch.core = cast(Any, Core())
        orch._ensure_instance_secret_sync(self._spec())
        assert calls == ["create", "patch"]
        assert body_holder["stringData"] == {"JUPYTER_TOKEN": "plain-token-1"}


class TestDataDiskPvc:
    """一盘一 PVC 的下发面:建 / 幂等 / 只扩不缩。挂了 = 配额语义(PVC 容量)被破坏。"""

    def _orch_with_pvc(self, existing_gi: int | None) -> tuple[Any, list[tuple[str, Any]]]:
        orch = _bare()
        calls: list[tuple[str, Any]] = []

        def read(name: str, ns: str) -> Any:
            if existing_gi is None:
                raise k8s_client.ApiException(status=404)
            return SimpleNamespace(
                spec=SimpleNamespace(
                    resources=SimpleNamespace(requests={"storage": f"{existing_gi}Gi"})
                )
            )

        orch.core = cast(
            Any,
            SimpleNamespace(
                read_namespaced_persistent_volume_claim=read,
                create_namespaced_persistent_volume_claim=lambda ns, pvc: calls.append(
                    ("create", pvc.spec.resources.requests["storage"])
                ),
                patch_namespaced_persistent_volume_claim=lambda name, ns, body: calls.append(
                    ("patch", body["spec"]["resources"]["requests"]["storage"])
                ),
            ),
        )
        return orch, calls

    def test_creates_when_absent(self):
        orch, calls = self._orch_with_pvc(None)
        orch._ensure_data_disk_sync("tenant-u1", "disk-abc", 10)
        assert calls == [("create", "10Gi")]

    def test_idempotent_when_same_size(self):
        orch, calls = self._orch_with_pvc(10)
        orch._ensure_data_disk_sync("tenant-u1", "disk-abc", 10)
        assert calls == []

    def test_expands_when_smaller(self):
        orch, calls = self._orch_with_pvc(10)
        orch._ensure_data_disk_sync("tenant-u1", "disk-abc", 50)
        assert calls == [("patch", "50Gi")]

    def test_never_shrinks(self):
        """缩容会被 apiserver 拒;这里先于请求拦住,免得反复进死信。"""
        orch, calls = self._orch_with_pvc(50)
        orch._ensure_data_disk_sync("tenant-u1", "disk-abc", 10)
        assert calls == []


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


def _orch_with(pods: list[Any]) -> RealOrchestrator:
    """挂 CoreStub 的裸 RealOrchestrator。"""
    orch = _bare()

    class CoreStub:
        def list_pod_for_all_namespaces(self, **kwargs: Any) -> Any:
            return _page(pods)

    orch.core = cast(Any, CoreStub())
    return orch


class TestHamiCapacityAccounting:
    """HAMi 池容量:物理卡数取 GFD 标签,已用份额按 gpucores 折算。"""

    def _node(self, *, allocatable_gpu: int, gfd_count: str | None) -> Any:
        labels = {POOL_NODE_LABEL: "hami"}
        if gfd_count is not None:
            labels["nvidia.com/gpu.count"] = gfd_count
        return SimpleNamespace(
            metadata=SimpleNamespace(name="hami-n1", labels=labels),
            status=SimpleNamespace(allocatable={"nvidia.com/gpu": str(allocatable_gpu)}),
        )

    def test_physical_count_prefers_gfd_label(self):
        # 2 物理卡 × deviceSplitCount 10 → allocatable 20;物理口径 2
        node = self._node(allocatable_gpu=20, gfd_count="2")
        assert RealOrchestrator._physical_gpu_amount(node) == 2

    def test_no_gfd_label_falls_back_to_allocatable(self):
        """非切分池(kata)缺 GFD 标签:allocatable 即物理数。"""
        node = SimpleNamespace(
            metadata=SimpleNamespace(name="kata-n1", labels={POOL_NODE_LABEL: "kata"}),
            status=SimpleNamespace(allocatable={"nvidia.com/gpu": "8"}),
        )
        assert RealOrchestrator._physical_gpu_amount(node) == 8

    def test_hami_pool_missing_gfd_label_refused(self):
        """hami 池缺 GFD 标签:拒纳管计 0。"""
        node = self._node(allocatable_gpu=80, gfd_count=None)
        assert RealOrchestrator._physical_gpu_amount(node) == 0

    def test_gfd_version_full_label(self):
        labels = {
            "nvidia.com/cuda.driver-version.full": "610.57.04",
            "nvidia.com/cuda.runtime-version.full": "13.3",
        }
        assert RealOrchestrator._gfd_version(labels, "driver") == "610.57.04"
        assert RealOrchestrator._gfd_version(labels, "runtime") == "13.3"

    def test_gfd_version_composed_from_parts(self):
        """GFD 只发 major/minor(/revision)时拼回完整版本号,缺项不留空段。"""
        labels = {
            "nvidia.com/cuda.driver-version.major": "610",
            "nvidia.com/cuda.driver-version.minor": "57",
            "nvidia.com/cuda.runtime-version.major": "13",
            "nvidia.com/cuda.runtime-version.minor": "3",
        }
        assert RealOrchestrator._gfd_version(labels, "driver") == "610.57"
        assert RealOrchestrator._gfd_version(labels, "runtime") == "13.3"
        assert RealOrchestrator._gfd_version({}, "driver") == ""

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

    def test_used_by_node_ceils_after_share_sum(self):
        """份额先求和再向上取整(0.5 + 0.3 → 1);未调度 Pod 不归节点。"""
        pods = [
            _pod(pool="hami", node_name="n1", gpu=1, cores=50),
            _pod(pool="hami", node_name="n1", gpu=1, cores=30),
            _pod(pool=None, node_name=None, gpu=9),
        ]
        assert _orch_with(pods)._used_gpus_by_node() == {"n1": 1}


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
    """SSH Service 的 409/422 后读回 nodePort 核对,漂移则 patch。"""

    def _orch(self, existing: Any, create_exc: k8s_client.ApiException | None = None) -> Any:
        """existing 传 ApiException 表示读回时抛该异常。"""
        orch = _bare()
        calls: dict[str, list] = {"patch": []}

        class CoreStub:
            def create_namespaced_service(self, ns: str, svc: Any) -> None:
                # 只测 SSH(NodePort);jupyter(ClusterIP)恒 409
                if svc.spec.type != "NodePort":
                    raise _api_exc(409)
                raise create_exc or _api_exc(409)

            def read_namespaced_service(self, name: str, ns: str) -> Any:
                if isinstance(existing, k8s_client.ApiException):
                    raise existing
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

    def test_replayed_create_keeps_own_port(self):
        """同名 Service 已持有期望端口时 422 = 幂等成功,不换端口。"""
        orch, calls = self._orch(
            self._existing_svc(31001), _api_exc(422, "provided port is already allocated")
        )
        orch._create_service_sync(_spec(31001))  # 不抛错
        assert calls["patch"] == []

    def test_port_taken_by_other_object_raises_nodeporttaken(self):
        """422 且同名 Service 读回 404 = 端口被占,换端口。"""
        orch, _calls = self._orch(
            _api_exc(404), _api_exc(422, "provided port is already allocated")
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
    """ResourceQuota 含 cpu/memory/ephemeral;patch 收敛存量 ns。"""

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


class TestServiceWorkloadObjects:
    """服务型实例(workload_type='service')的对象规约。"""

    def _dev(self, **over: Any) -> InstancePodSpec:
        base: dict[str, Any] = {
            "namespace": "tenant-1",
            "name": "inst-1",
            "image": "img",
            "gpu_resources": {},
            "runtime_class": None,
            "host_users": True,
            "vcpu": 1,
            "mem_gb": 1,
            "disk_gb": 1,
            "ssh_node_port": 31234,
            "jupyter_host": "inst-1.app.example.com",
        }
        base.update(over)
        return InstancePodSpec(**base)

    def _svc(self, **over: Any) -> InstancePodSpec:
        svc: dict[str, Any] = {
            "restart_policy": "Always",
            "service_port": 8000,
            "service_host": "svc-abc123.svc.example.com",
            "with_ssh": False,
            "ssh_node_port": None,
        }
        svc.update(over)
        return self._dev(**svc)

    def test_dev_route_targets_jupyter_listener(self):
        body = _bare()._httproute_body(self._dev())
        parent = body["spec"]["parentRefs"][0]
        assert parent["sectionName"] == "app-https"
        assert body["spec"]["hostnames"] == ["inst-1.app.example.com"]
        assert body["spec"]["rules"][0]["backendRefs"] == [{"name": "inst-1-jupyter", "port": 8888}]

    def test_service_route_targets_svc_listener(self):
        """HTTPRoute 挂在带 extAuth 的 listener。"""
        body = _bare()._httproute_body(self._svc())
        parent = body["spec"]["parentRefs"][0]
        assert parent["sectionName"] == "svc-https"
        assert body["spec"]["hostnames"] == ["svc-abc123.svc.example.com"]
        assert body["spec"]["rules"][0]["backendRefs"] == [{"name": "inst-1-svc", "port": 8000}]

    def test_service_route_without_host_refuses(self):
        """service_port 有而 service_host 空:创建失败,不建无 hostname 的 HTTPRoute。"""
        with pytest.raises(RuntimeError, match="service_host"):
            _bare()._httproute_body(self._svc(service_host=None))

    def _services(self, spec: InstancePodSpec) -> dict[str, Any]:
        created: dict[str, Any] = {}

        class Core:
            def create_namespaced_service(self, ns: str, svc: Any) -> None:
                created[svc.metadata.name] = svc

        orch = _bare()
        orch.core = cast(Any, Core())
        orch._create_service_sync(spec)
        return created

    def test_dev_builds_ssh_and_jupyter_services(self):
        created = self._services(self._dev())
        assert set(created) == {"inst-1", "inst-1-jupyter"}
        assert created["inst-1"].spec.ports[0].node_port == 31234

    def test_service_without_ssh_builds_only_endpoint_service(self):
        """不开 SSH 不建 NodePort Service。"""
        created = self._services(self._svc())
        assert set(created) == {"inst-1-svc"}
        assert created["inst-1-svc"].spec.type == "ClusterIP"
        assert created["inst-1-svc"].spec.ports[0].port == 8000

    def test_service_with_ssh_builds_both_but_no_jupyter(self):
        created = self._services(self._svc(with_ssh=True, ssh_node_port=31500))
        assert set(created) == {"inst-1", "inst-1-svc"}

    def test_service_wanting_ssh_without_port_refuses(self):
        with pytest.raises(RuntimeError, match="node port"):
            self._services(self._svc(with_ssh=True))

    def _pod(self, spec: InstancePodSpec) -> Any:
        created: dict[str, Any] = {}

        class Core:
            def create_namespaced_pod(self, ns: str, pod: Any) -> None:
                created["pod"] = pod

        orch = _bare()
        orch.core = cast(Any, Core())
        orch._create_pod_sync(spec)
        return created["pod"]

    def test_dev_pod_restart_never_no_probes(self):
        pod = self._pod(self._dev())
        assert pod.spec.restart_policy == "Never"
        c = pod.spec.containers[0]
        assert c.command is None and c.args is None
        assert c.startup_probe is None and c.readiness_probe is None
        assert {p.name for p in c.ports} == {"ssh", "jupyter"}

    def test_service_pod_restart_always_and_command(self):
        """restartPolicy=Always(Pod 名 = 实例 uuid,不可重建换名)。"""
        pod = self._pod(
            self._svc(command=("python",), args=("-m", "vllm.entrypoints.openai.api_server"))
        )
        assert pod.spec.restart_policy == "Always"
        c = pod.spec.containers[0]
        assert c.command == ["python"]
        assert c.args == ["-m", "vllm.entrypoints.openai.api_server"]
        # 服务型实例不声明 8888
        assert {p.name for p in c.ports} == {"svc"}

    def test_health_path_yields_startup_and_readiness(self):
        """startupProbe 的 failureThreshold 远大于 readiness 的。"""
        c = self._pod(self._svc(health_path="/health")).spec.containers[0]
        assert c.startup_probe.http_get.path == "/health"
        assert c.startup_probe.http_get.port == 8000
        assert c.readiness_probe.http_get.path == "/health"
        assert c.startup_probe.failure_threshold > c.readiness_probe.failure_threshold
        assert c.startup_probe.failure_threshold * c.startup_probe.period_seconds >= 600

    def test_no_health_path_yields_no_probes(self):
        c = self._pod(self._svc()).spec.containers[0]
        assert c.startup_probe is None and c.readiness_probe is None


class TestDataDiskMount:
    """挂数据盘的实例 Pod:直挂该盘自己的 PVC、不带 subPath,且仍满足租户准入基线。
    挂了说明:要么盘挂错(串到别人的盘),要么 Pod 被 superdl-tenant-pod-baseline 拒掉。"""

    def _pod_with_disk(self) -> Any:
        from app.core.k8s.real import build_instance_pod

        # runc 档(hami/mig/cpu)一律 host_users=False,数据盘就是挂进这种 Pod 的
        spec = InstancePodSpec(
            **{**_spec().__dict__, "data_disk_pvc": "disk-" + "a" * 32, "host_users": False}
        )
        return build_instance_pod(spec)

    def test_mounts_own_pvc_without_subpath(self):
        pod = self._pod_with_disk()
        pod_spec = cast(Any, pod).spec
        vol = next(v for v in pod_spec.volumes if v.name == "data-disk")
        assert vol.persistent_volume_claim.claim_name == "disk-" + "a" * 32
        mount = next(m for m in pod_spec.containers[0].volume_mounts if m.name == "data-disk")
        assert mount.mount_path == "/root/data"
        # subPath 是共享 PVC 时代的产物;一盘一 PVC 后再带它就是挂错层级
        assert mount.sub_path is None

    def test_keeps_tenant_baseline(self):
        pod_spec = cast(Any, self._pod_with_disk()).spec
        assert pod_spec.host_users is False
        assert pod_spec.automount_service_account_token is False

    def test_no_data_disk_means_no_volume(self):
        from app.core.k8s.real import build_instance_pod

        pod_spec = cast(Any, build_instance_pod(_spec())).spec
        assert all(v.name != "data-disk" for v in pod_spec.volumes)


class TestInstancePodBandwidth:
    """带宽注解随 spec.annotations 落到 Pod。挂了说明:限速注解丢了,租户出向无上限。"""

    def test_annotations_reach_pod_metadata(self):
        from app.core.k8s.real import build_instance_pod

        spec = _spec()
        spec = InstancePodSpec(
            **{**spec.__dict__, "annotations": {"kubernetes.io/egress-bandwidth": "200M"}}
        )
        pod = cast(Any, build_instance_pod(spec))
        assert pod.metadata.annotations["kubernetes.io/egress-bandwidth"] == "200M"
        assert pod.spec.host_users is None  # host_users=True 的样板不写该字段
