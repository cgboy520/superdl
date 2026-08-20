"""内存态 FakeOrchestrator:dev/test 默认后端。

行为可注入:auto_ready(Pod 立即 Ready)、fail_create(下次创建失败)、
kill_pod / inject_pod(reconciler 场景)。容量按 pool 配置,近似库存=容量-已用。
"""

from dataclasses import dataclass, field

from app.core.k8s.base import InstancePodSpec, PodStatus, PrewarmJobStatus


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
    wiped_disks: list[tuple[str, str]] = field(default_factory=list)
    # 预热(WP22):(node_name, image_ref) -> state;auto_prewarm=True 时创建即 succeeded
    prewarm_jobs: dict[tuple[str, str], str] = field(default_factory=dict)
    prewarm_calls: list[tuple[str, str]] = field(default_factory=list)
    auto_prewarm: bool = True
    fail_next_prewarm: bool = False
    # 节点注入(WP23 加入对账测试):追加在合成节点之后
    extra_nodes: list = field(default_factory=list)
    # cordon 状态(WP23):节点名集合,list_nodes 反映为 Cordoned
    cordoned_nodes: set[str] = field(default_factory=set)

    async def ensure_namespace(self, namespace: str) -> None:
        self.namespaces.add(namespace)

    async def wipe_disk(self, namespace: str, subpath: str) -> None:
        self.wiped_disks.append((namespace, subpath))

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

    # ---------- 预热(WP22) ----------

    async def prewarm_image(self, node_name: str, image_ref: str) -> None:
        if self.fail_next_prewarm:
            self.fail_next_prewarm = False
            raise RuntimeError("fake: prewarm_image failed (injected)")
        self.prewarm_calls.append((node_name, image_ref))
        # setdefault = 幂等:已有 Job(任意状态)不重建
        self.prewarm_jobs.setdefault(
            (node_name, image_ref), "succeeded" if self.auto_prewarm else "running"
        )

    async def get_prewarm_status(self, node_name: str, image_ref: str) -> PrewarmJobStatus:
        state = self.prewarm_jobs.get((node_name, image_ref))
        if state is None:
            return PrewarmJobStatus(state="absent")
        message = "fake: ErrImagePull" if state == "failed" else None
        return PrewarmJobStatus(state=state, message=message)

    async def delete_prewarm_job(self, node_name: str, image_ref: str) -> None:
        self.prewarm_jobs.pop((node_name, image_ref), None)

    def set_prewarm_state(self, node_name: str, image_ref: str, state: str) -> None:
        """测试注入:直接改 Job 状态(running/succeeded/failed)。"""
        self.prewarm_jobs[(node_name, image_ref)] = state

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
        nodes.extend(self.extra_nodes)
        return [
            NodeInfo(
                name=n.name,
                pool_label=n.pool_label,
                gpu_model=n.gpu_model,
                gpu_total=n.gpu_total,
                gpu_used=n.gpu_used,
                status="Cordoned" if n.name in self.cordoned_nodes else n.status,
            )
            for n in nodes
        ]

    def inject_node(self, node) -> None:
        """模拟新 GPU 节点加入集群(WP23 对账测试)。传 NodeInfo。"""
        self.extra_nodes.append(node)

    async def set_node_unschedulable(self, node_name: str, unschedulable: bool) -> None:
        if unschedulable:
            self.cordoned_nodes.add(node_name)
        else:
            self.cordoned_nodes.discard(node_name)
