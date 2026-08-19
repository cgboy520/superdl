"""内存态 FakeOrchestrator:dev/test 默认后端。

行为可注入:auto_ready(Pod 立即 Ready)、fail_create(下次创建失败)、
kill_pod / inject_pod(reconciler 场景)。容量按 pool 配置,近似库存=容量-已用。
"""

from dataclasses import dataclass, field

from app.core.k8s.base import InstancePodSpec, PodStatus


@dataclass
class _FakePod:
    spec: InstancePodSpec
    ready: bool
    phase: str = "Running"
    node_name: str = "fake-node-1"


@dataclass
class FakeOrchestrator:
    auto_ready: bool = True
    fail_next_create: bool = False
    pool_capacity: dict[str, int] = field(
        default_factory=lambda: {"kata": 16, "hami": 32, "mig": 16}
    )
    pods: dict[tuple[str, str], _FakePod] = field(default_factory=dict)
    namespaces: set[str] = field(default_factory=set)
    # 统计(测试断言用)
    create_calls: int = 0
    delete_calls: int = 0

    async def ensure_namespace(self, namespace: str) -> None:
        self.namespaces.add(namespace)

    async def create_instance(self, spec: InstancePodSpec) -> None:
        if self.fail_next_create:
            self.fail_next_create = False
            raise RuntimeError("fake: create_instance failed (injected)")
        self.create_calls += 1
        key = (spec.namespace, spec.name)
        if key in self.pods:
            return  # 幂等
        self.pods[key] = _FakePod(
            spec=spec, ready=self.auto_ready, phase="Running" if self.auto_ready else "Pending"
        )

    async def delete_instance(self, namespace: str, name: str) -> None:
        self.delete_calls += 1
        self.pods.pop((namespace, name), None)

    async def get_status(self, namespace: str, name: str) -> PodStatus:
        pod = self.pods.get((namespace, name))
        if pod is None:
            return PodStatus(exists=False)
        return PodStatus(exists=True, ready=pod.ready, phase=pod.phase, node_name=pod.node_name)

    async def list_instance_pods(self) -> list[tuple[str, str]]:
        return list(self.pods)

    async def available_gpus(self, pool_label: str) -> int:
        cap = self.pool_capacity.get(pool_label, 0)
        used = sum(
            int(p.spec.gpu_resources.get("nvidia.com/gpu", "0"))
            for p in self.pods.values()
            if p.spec.node_selector.get("superdl.io/pool") == pool_label
        )
        return max(0, cap - used)

    # ---------- 测试注入 ----------

    def kill_pod(self, namespace: str, name: str) -> None:
        """模拟 Pod 意外消失(节点故障)。"""
        self.pods.pop((namespace, name), None)

    def mark_ready(self, namespace: str, name: str) -> None:
        pod = self.pods[(namespace, name)]
        pod.ready = True
        pod.phase = "Running"

    def inject_leaked_pod(self, namespace: str, name: str, spec: InstancePodSpec) -> None:
        """模拟 DB 已 released 但 K8s 残留的泄漏 Pod。"""
        self.pods[(namespace, name)] = _FakePod(spec=spec, ready=True)

    async def list_nodes(self):
        """管理端节点视图(Fake:按池合成节点)。"""
        from app.core.k8s.base import NodeInfo

        models = {"kata": "RTX4090", "hami": "RTX4090", "mig": "H100"}
        nodes = []
        for pool, cap in self.pool_capacity.items():
            used = cap - await self.available_gpus(pool)
            nodes.append(
                NodeInfo(
                    name=f"fake-{pool}-node-1",
                    pool_label=pool,
                    gpu_model=models.get(pool, "GPU"),
                    gpu_total=cap,
                    gpu_used=used,
                    status="Ready",
                )
            )
        return nodes
