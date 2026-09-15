"""内存态 K8s 后端;支持 Pod、节点、预热和故障注入。

fail_next_disk 和 fail_next_logs 消费后复位;fail_probe 持续生效直到显式清除。
"""

from dataclasses import dataclass, field

from app.core.k8s import health
from app.core.k8s.base import (
    DATA_DISK_STORAGE_CLASS,
    GPU_MODEL_NODE_LABEL,
    INSTANCE_DISK_STORAGE_CLASS,
    MANAGED_LABEL,
    POOL_NODE_LABEL,
    ClusterProbe,
    ComponentDetail,
    ComponentFact,
    ComponentObject,
    InstancePodSpec,
    NodeInfo,
    NodePortTaken,
    PodStatus,
    PrewarmJobStatus,
    derive_distro,
)
from app.core.k8s.health import (
    ListenerRow,
    NodeRow,
    RuntimeClassRow,
    StorageClassRow,
    WorkloadRow,
)

_FAKE_K8S_VERSION = "v1.36.2+rke2r1"
_DETAIL_CAPABLE = frozenset(
    {
        "hami",
        "gpu_operator",
        "dcgm",
        "kata_runtimeclass",
        "storage",
        "gateway",
        "cert_manager",
        "monitoring",
    }
)
_FAKE_LISTENERS = (
    ("http", 80, "HTTP"),
    ("api-https", 443, "HTTPS"),
    ("console-https", 443, "HTTPS"),
    ("admin-https", 443, "HTTPS"),
    ("app-https", 443, "HTTPS"),
    ("svc-https", 443, "HTTPS"),
)


_FAKE_PROVISIONERS = {
    INSTANCE_DISK_STORAGE_CLASS: "topolvm.io",
    DATA_DISK_STORAGE_CLASS: "rook-ceph.cephfs.csi.ceph.com",
}


def _fake_ds(name: str, ready: int, desired: int, image: str = "") -> WorkloadRow:
    return WorkloadRow(name, "gpu-operator", ready, desired, image)


@dataclass
class _FakePod:
    spec: InstancePodSpec
    ready: bool
    phase: str = "Running"
    node_name: str = "fake-node-1"
    deleting: bool = False
    labels: dict[str, str] = field(default_factory=lambda: {MANAGED_LABEL: "true"})


@dataclass
class FakeOrchestrator:
    auto_ready: bool = True
    graceful_delete: bool = False
    pool_capacity: dict[str, int] = field(
        default_factory=lambda: {"kata": 16, "hami": 32, "mig": 16, "cpu": 0}
    )
    pods: dict[tuple[str, str], _FakePod] = field(default_factory=dict)
    namespaces: set[str] = field(default_factory=set)
    instance_disks: dict[tuple[str, str], str] = field(default_factory=dict)
    data_disks: dict[tuple[str, str], int] = field(default_factory=dict)
    deleted_data_disks: list[tuple[str, str]] = field(default_factory=list)
    fail_next_disk: bool = False
    job_pods: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)
    prewarm_jobs: dict[tuple[str, str], str] = field(default_factory=dict)
    prewarm_pull_secrets: dict[tuple[str, str], str | None] = field(default_factory=dict)
    pull_secrets: dict[str, str] = field(default_factory=dict)
    auto_prewarm: bool = True
    extra_nodes: list = field(default_factory=list)
    unlabeled_nodes: list = field(default_factory=list)
    node_labels: dict[str, dict[str, str]] = field(default_factory=dict)
    cordoned_nodes: set[str] = field(default_factory=set)
    deleted_nodes: set[str] = field(default_factory=set)
    endpoints: set[tuple[str, str]] = field(default_factory=set)
    external_node_ports: set[int] = field(default_factory=set)
    instance_secrets: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)
    probe_hami_ready: bool = True
    probe_gpu_operand_ready: int | None = None
    probe_unprogrammed_listeners: tuple[str, ...] = ()
    probe_storage_classes: tuple[str, ...] = (
        INSTANCE_DISK_STORAGE_CLASS,
        DATA_DISK_STORAGE_CLASS,
    )
    detail_pods: dict[str, tuple[ComponentObject, ...]] = field(default_factory=dict)
    detail_events: dict[str, tuple[ComponentObject, ...]] = field(default_factory=dict)
    probe_k8s_version: str = _FAKE_K8S_VERSION
    fail_probe: bool = False
    fail_next_logs: bool = False
    log_calls: list[tuple[str, str, int]] = field(default_factory=list)

    async def ensure_namespace(self, namespace: str) -> None:
        self.namespaces.add(namespace)

    async def ensure_pull_secret(
        self,
        namespace: str,
        dockerconfigjson: str,  # noqa: ARG002
        fingerprint: str,
    ) -> None:
        self.pull_secrets[namespace] = fingerprint

    async def probe_cluster(self) -> ClusterProbe:
        if self.fail_probe:
            return ClusterProbe(api_reachable=False, error="fake: connection refused")
        rows = await self._fake_probe_rows()
        pools, pools_ready = health.pool_counts(rows.nodes)
        return ClusterProbe(
            api_reachable=True,
            k8s_version=self.probe_k8s_version,
            distro=derive_distro(self.probe_k8s_version),
            hami_ready=self.probe_hami_ready,
            dcgm_present=bool(rows.dcgm),
            kps_present=True,
            gpu_operator_present=True,
            kata_runtimeclass=True,
            nvidia_runtimeclass=True,
            gateway_ready=rows.gateway_programmed,
            cert_manager_ready=True,
            nodes_ready=sum(pools_ready.values()),
            nodes_total=len(rows.nodes),
            storage_classes=tuple(r.name for r in rows.storage_classes),
            pools=pools,
            pools_ready=pools_ready,
            component_facts=health.build_facts(
                rows,
                instance_disk_sc=INSTANCE_DISK_STORAGE_CLASS,
                data_disk_sc=DATA_DISK_STORAGE_CLASS,
            ),
            error=None,
        )

    async def probe_component_detail(self, key: str) -> ComponentDetail:
        """合成深探结果:默认全就绪(空表);fail_probe 时抛,供 503 降级路径断言。"""
        if self.fail_probe:
            raise RuntimeError("fake: connection refused")
        rows = await self._fake_probe_rows()
        pods = self.detail_pods.get(key, ())
        if key == "nodes":
            return ComponentDetail(
                facts=(ComponentFact(key="nodesWithPressure", value=str(len(pods))),),
                pods=pods,
            )
        if key not in _DETAIL_CAPABLE:
            return ComponentDetail()
        total = len(rows.nodes) if key != "cert_manager" else 3
        return ComponentDetail(
            facts=(
                ComponentFact(key="podsTotal", value=str(total)),
                ComponentFact(
                    key="podsNotReady", value=str(len(pods)), tone="bad" if pods else "normal"
                ),
            ),
            pods=pods,
            events=self.detail_events.get(key, ()),
        )

    async def _fake_probe_rows(self) -> health.ProbeRows:
        """合成与 real 同形状的探测行:节点由 pool_capacity 派生,工作负载按节点数铺开。"""
        nodes = [
            NodeRow(
                name=n.name,
                pool=n.pool_label if n.pool_label not in ("", "unknown") else "unlabeled",
                ready=n.status != "NotReady",
                schedulable=n.status != "Cordoned",
                kubelet=self.probe_k8s_version,
                reason="" if n.status == "Ready" else n.status,
            )
            for n in await self.list_nodes(include_unlabeled=True)
        ]
        _, pools_ready = health.pool_counts(nodes)
        gpu_nodes = sum(v for k, v in pools_ready.items() if k in ("hami", "kata", "mig"))
        operand_ready = (
            gpu_nodes if self.probe_gpu_operand_ready is None else self.probe_gpu_operand_ready
        )
        dcgm = [_fake_ds("nvidia-dcgm-exporter", operand_ready, gpu_nodes, "dcgm-exporter:4.8.3")]
        operands = [
            _fake_ds(name, operand_ready, gpu_nodes, "gpu-operator:25.3.0")
            for name in ("gpu-feature-discovery", "nvidia-device-plugin-daemonset")
        ] + dcgm
        return health.ProbeRows(
            nodes=nodes,
            hami_scheduler=WorkloadRow(
                "hami-scheduler", "kube-system", int(self.probe_hami_ready), 1, "hami:2.9"
            ),
            hami_device_plugin=_fake_ds(
                "hami-device-plugin", pools_ready.get("hami", 0), pools_ready.get("hami", 0)
            ),
            gpu_operands=operands,
            dcgm=dcgm,
            cert_manager=[
                WorkloadRow(name, "cert-manager", 1, 1, "cert-manager:v1.19.1")
                for name in ("cert-manager", "cert-manager-webhook", "cert-manager-cainjector")
            ],
            kata_deploy=_fake_ds(
                "kata-deploy", pools_ready.get("kata", 0), pools_ready.get("kata", 0)
            ),
            prometheus=WorkloadRow("prometheus-kps", "monitoring", 1, 1),
            alertmanager=WorkloadRow("alertmanager-kps", "monitoring", 1, 1),
            runtime_classes=[
                RuntimeClassRow("nvidia", "nvidia"),
                RuntimeClassRow("kata-qemu", "kata-qemu"),
            ],
            storage_classes=[
                StorageClassRow(name, _FAKE_PROVISIONERS.get(name, "fake.io"), "Immediate")
                for name in self.probe_storage_classes
            ],
            gateway_programmed=True,
            gateway_address="10.0.0.1",
            listeners=[
                ListenerRow(
                    name=name,
                    port=port,
                    protocol=proto,
                    attached=1,
                    programmed=name not in self.probe_unprogrammed_listeners,
                    reason="" if name not in self.probe_unprogrammed_listeners else "Invalid",
                )
                for name, port, proto in _FAKE_LISTENERS
            ],
            gateway_reason="",
            allocatable_gpu=sum(self.pool_capacity.values()),
            driver_version="580.173.02",
        )

    async def ensure_data_disk(self, namespace: str, name: str, size_gb: int) -> None:
        if self.fail_next_disk:
            self.fail_next_disk = False
            raise RuntimeError("fake: ensure_data_disk failed (injected)")
        self.data_disks[(namespace, name)] = max(self.data_disks.get((namespace, name), 0), size_gb)

    async def delete_data_disk(self, namespace: str, name: str) -> None:
        self.data_disks.pop((namespace, name), None)
        self.deleted_data_disks.append((namespace, name))

    async def create_instance(self, spec: InstancePodSpec) -> None:
        if spec.with_ssh and spec.ssh_node_port is None:
            raise RuntimeError(f"fake: instance {spec.name} wants ssh but has no node port")
        if spec.service_port is not None and not spec.service_host:
            raise RuntimeError(f"fake: instance {spec.name} has service_port but no service_host")
        if spec.ssh_node_port is not None and spec.ssh_node_port in self.external_node_ports:
            raise NodePortTaken(spec.ssh_node_port)
        key = (spec.namespace, spec.name)
        if spec.secret_env:
            self.instance_secrets[key] = dict(spec.secret_env)
        self.instance_disks.setdefault(key, f"lv-{spec.name}")
        existing = self.pods.get(key)
        if existing is not None:
            if existing.deleting:
                raise RuntimeError(f"fake: pod {spec.name} is terminating, create must wait")
            return
        self.pods[key] = _FakePod(
            spec=spec, ready=self.auto_ready, phase="Running" if self.auto_ready else "Pending"
        )
        self.endpoints.add(key)

    async def delete_instance(self, namespace: str, name: str, *, force: bool = False) -> None:
        if self.graceful_delete and not force:
            pod = self.pods.get((namespace, name))
            if pod is not None:
                pod.deleting = True
                pod.ready = False
            return
        self.pods.pop((namespace, name), None)
        self.instance_secrets.pop((namespace, name), None)
        self.endpoints.discard((namespace, name))

    def finish_delete(self, namespace: str, name: str) -> None:
        """移除内存中的 Pod;保留端点、Secret 和实例盘记录。"""
        self.pods.pop((namespace, name), None)

    async def list_instance_endpoints(self) -> list[tuple[str, str]]:
        return sorted(self.endpoints)

    async def used_node_ports(self) -> set[int]:
        return {
            p.spec.ssh_node_port for p in self.pods.values() if p.spec.ssh_node_port is not None
        } | set(self.external_node_ports)

    def inject_external_port(self, port: int) -> None:
        """测试注入:集群里出现一个非平台对象占用了该 NodePort。"""
        self.external_node_ports.add(port)

    async def delete_instance_disk(self, namespace: str, name: str) -> None:
        self.instance_disks.pop((namespace, name), None)

    async def get_status(self, namespace: str, name: str) -> PodStatus:
        pod = self.pods.get((namespace, name))
        if pod is None:
            return PodStatus(exists=False)
        return PodStatus(
            exists=True,
            ready=pod.ready,
            phase=pod.phase,
            node_name=pod.node_name,
            deleting=pod.deleting,
            namespace=namespace,
            name=name,
        )

    async def read_instance_logs(self, namespace: str, name: str, *, tail_lines: int) -> str:
        """合成日志(带时间戳的固定几行),不按 Pod 存在性报错;失败路径由 fail_next_logs 注入。"""
        if self.fail_next_logs:
            self.fail_next_logs = False
            raise RuntimeError("fake: read_instance_logs failed (injected)")
        self.log_calls.append((namespace, name, tail_lines))
        lines = [
            f"2026-08-23T03:14:01Z [entrypoint] instance {name} booting",
            "2026-08-23T03:14:01Z [entrypoint] mounting instance disk at /root",
            "2026-08-23T03:14:02Z [entrypoint] starting sshd on :22",
            "2026-08-23T03:14:02Z [sshd] Server listening on 0.0.0.0 port 22",
            "2026-08-23T03:14:03Z [entrypoint] starting jupyter…",
            "2026-08-23T03:14:03Z [jupyter] Jupyter Server 2.16.0 is running at http://0.0.0.0:8888/lab",
            f"2026-08-23T03:14:04Z [jupyter] incoming websocket from console ({name})",
            "2026-08-23T03:14:05Z [entrypoint] bootstrap done, workspace ready",
        ]
        return "\n".join(lines[-tail_lines:])

    async def list_instance_pods(self) -> list[PodStatus]:
        entries = [
            PodStatus(
                exists=True,
                ready=pod.ready,
                phase=pod.phase,
                node_name=pod.node_name,
                deleting=pod.deleting,
                namespace=ns,
                name=name,
                labels=dict(pod.labels),
            )
            for (ns, name), pod in self.pods.items()
        ]
        entries.extend(
            PodStatus(
                exists=True,
                phase="Running",
                node_name="fake-node-1",
                namespace=ns,
                name=name,
                labels=dict(labels),
            )
            for (ns, name), labels in self.job_pods.items()
        )
        return entries

    async def prewarm_image(
        self, node_name: str, image_ref: str, *, image_pull_secret: str | None = None
    ) -> None:
        self.prewarm_pull_secrets[(node_name, image_ref)] = image_pull_secret
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

    def kill_pod(self, namespace: str, name: str) -> None:
        """模拟 Pod 意外消失(节点故障)。"""
        self.pods.pop((namespace, name), None)

    def mark_unready(self, namespace: str, name: str) -> None:
        """模拟节点失联:kubelet 不可达,Ready 转 False 而 phase 仍是 Running、对象仍在。"""
        pod = self.pods[(namespace, name)]
        pod.ready = False
        pod.phase = "Running"

    def mark_ready(self, namespace: str, name: str) -> None:
        pod = self.pods[(namespace, name)]
        pod.ready = True
        pod.phase = "Running"

    def inject_leaked_pod(self, namespace: str, name: str, spec: InstancePodSpec) -> None:
        """模拟 DB 已 released 但 K8s 残留的泄漏 Pod。"""
        self.pods[(namespace, name)] = _FakePod(spec=spec, ready=True)

    async def list_nodes(self, include_unlabeled: bool = False) -> list[NodeInfo]:
        """节点视图(Fake:按池合成节点;include_unlabeled 时附无标签节点)。"""
        models = {"kata": "RTX4090", "hami": "RTX4090", "mig": "H100"}
        nodes = []
        for pool, cap in self.pool_capacity.items():
            used = min(
                cap,
                sum(
                    int(p.spec.gpu_resources.get("nvidia.com/gpu", "0"))
                    for p in self.pods.values()
                    if p.spec.node_selector.get(POOL_NODE_LABEL) == pool
                ),
            )
            name = f"fake-{pool}-node-1"
            nodes.append(
                NodeInfo(
                    name=name,
                    pool_label=pool,
                    gpu_total=cap,
                    gpu_used=used,
                    status="Ready",
                    vcpu=64,
                    mem_gb=512,
                    disk_gb=2048,
                    gpu_model_label=models.get(pool, ""),
                    model_label_current=self.node_labels.get(name, {}).get(
                        GPU_MODEL_NODE_LABEL, ""
                    ),
                )
            )
        if include_unlabeled:
            nodes.extend(self.unlabeled_nodes)
        nodes.extend(self.extra_nodes)
        return [
            NodeInfo(
                name=n.name,
                pool_label=n.pool_label,
                gpu_total=n.gpu_total,
                gpu_used=n.gpu_used,
                status="Cordoned" if n.name in self.cordoned_nodes else n.status,
                vcpu=n.vcpu,
                mem_gb=n.mem_gb,
                disk_gb=n.disk_gb,
                gpu_model_label=n.gpu_model_label,
                model_label_current=n.model_label_current
                or self.node_labels.get(n.name, {}).get(GPU_MODEL_NODE_LABEL, ""),
                driver_version_label=n.driver_version_label,
                cuda_version_label=n.cuda_version_label,
            )
            for n in nodes
            if n.name not in self.deleted_nodes
        ]

    def inject_node(self, node) -> None:
        """模拟新 GPU 节点加入集群。传 NodeInfo。"""
        self.extra_nodes.append(node)

    async def set_node_labels(self, node_name: str, labels: dict[str, str | None]) -> None:
        current = self.node_labels.setdefault(node_name, {})
        for key, value in labels.items():
            if value is None:
                current.pop(key, None)
            else:
                current[key] = value

    async def set_node_unschedulable(self, node_name: str, unschedulable: bool) -> None:
        if unschedulable:
            self.cordoned_nodes.add(node_name)
        else:
            self.cordoned_nodes.discard(node_name)

    async def delete_node(self, node_name: str) -> None:
        """退役:先 cordon 再从节点视图里摘掉。幂等 —— 已删除的节点重放不报错。"""
        self.cordoned_nodes.add(node_name)
        self.deleted_nodes.add(node_name)
