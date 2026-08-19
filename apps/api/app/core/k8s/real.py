"""生产 K8s 编排(kubernetes 官方客户端 36.x,已对齐 K8s 1.36)。

官方客户端为同步实现,全部调用经 asyncio.to_thread 出让事件循环。
真实 GPU 行为(HAMi 限额/Kata 直通)只能实机验证 —— 人工事项 #2~#4。

对象命名:pod/svc/ingress 同名 = instance uuid;统一打标 superdl.io/instance。
"""

# pragma: no cover - 本文件需真实集群,单测不覆盖;kind 集成测试见人工清单

import asyncio
from typing import Any

from kubernetes import client, config

from app.core.config import get_settings
from app.core.k8s.base import InstancePodSpec, NodeInfo, PodStatus

INSTANCE_LABEL = "superdl.io/instance"
MANAGED_LABEL = "superdl.io/managed"
POOL_NODE_LABEL = "superdl.io/pool"


def _is_not_found(exc: client.ApiException) -> bool:
    return exc.status == 404


def _is_conflict(exc: client.ApiException) -> bool:
    return exc.status == 409


class RealOrchestrator:
    def __init__(self) -> None:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        self.core = client.CoreV1Api()
        self.net = client.NetworkingV1Api()
        self.settings = get_settings()

    # ---------- namespace ----------

    async def ensure_namespace(self, namespace: str) -> None:
        await asyncio.to_thread(self._ensure_namespace_sync, namespace)

    def _ensure_namespace_sync(self, namespace: str) -> None:
        try:
            self.core.create_namespace(
                client.V1Namespace(
                    metadata=client.V1ObjectMeta(name=namespace, labels={MANAGED_LABEL: "true"})
                )
            )
        except client.ApiException as exc:
            if not _is_conflict(exc):
                raise
        self._ensure_default_netpol_sync(namespace)

    def _ensure_default_netpol_sync(self, namespace: str) -> None:
        """默认拒东西向;放行出公网,禁访节点/Service 网段与云元数据(细则由 Cilium 集群策略补)。"""
        policy = client.V1NetworkPolicy(
            metadata=client.V1ObjectMeta(name="tenant-default", namespace=namespace),
            spec=client.V1NetworkPolicySpec(
                pod_selector=client.V1LabelSelector(),
                policy_types=["Ingress"],
                ingress=[],  # 拒全部东西向入方向;SSH/Jupyter 经 LB/Ingress 从北向进入
            ),
        )
        try:
            self.net.create_namespaced_network_policy(namespace, policy)
        except client.ApiException as exc:
            if not _is_conflict(exc):
                raise

    # ---------- instance ----------

    async def create_instance(self, spec: InstancePodSpec) -> None:
        await asyncio.to_thread(self._create_instance_sync, spec)

    def _create_instance_sync(self, spec: InstancePodSpec) -> None:
        self._create_pod_sync(spec)
        self._create_service_sync(spec)
        self._create_ingress_sync(spec)

    def _create_pod_sync(self, spec: InstancePodSpec) -> None:
        resources = {"cpu": str(spec.vcpu), "memory": f"{spec.mem_gb}Gi", **spec.gpu_resources}
        env = [client.V1EnvVar(name=k, value=v) for k, v in spec.env.items()]
        env.append(client.V1EnvVar(name="AUTHORIZED_KEYS", value="\n".join(spec.authorized_keys)))
        volumes: list[client.V1Volume] = [
            client.V1Volume(
                name="instance-disk",
                ephemeral=client.V1EphemeralVolumeSource(
                    volume_claim_template=client.V1PersistentVolumeClaimTemplate(
                        spec=client.V1PersistentVolumeClaimSpec(
                            access_modes=["ReadWriteOnce"],
                            storage_class_name="topolvm-provisioner",
                            resources=client.V1VolumeResourceRequirements(
                                requests={"storage": f"{spec.disk_gb}Gi"}
                            ),
                        )
                    )
                ),
            )
        ]
        mounts = [client.V1VolumeMount(name="instance-disk", mount_path="/root")]
        if spec.data_disk_subpath:
            volumes.append(
                client.V1Volume(
                    name="data-disk",
                    persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                        claim_name="juicefs-shared"
                    ),
                )
            )
            mounts.append(
                client.V1VolumeMount(
                    name="data-disk", mount_path="/root/data", sub_path=spec.data_disk_subpath
                )
            )
        pod = client.V1Pod(
            metadata=client.V1ObjectMeta(
                name=spec.name,
                namespace=spec.namespace,
                labels={INSTANCE_LABEL: spec.name, MANAGED_LABEL: "true"},
            ),
            spec=client.V1PodSpec(
                runtime_class_name=spec.runtime_class,
                host_users=spec.host_users if not spec.host_users else None,
                restart_policy="Never",
                node_selector=spec.node_selector or None,
                termination_grace_period_seconds=30,
                automount_service_account_token=False,
                enable_service_links=False,
                containers=[
                    client.V1Container(
                        name="workspace",
                        image=spec.image,
                        resources=client.V1ResourceRequirements(
                            limits=resources, requests=resources
                        ),
                        env=env,
                        ports=[
                            client.V1ContainerPort(container_port=22, name="ssh"),
                            client.V1ContainerPort(container_port=8888, name="jupyter"),
                        ],
                        volume_mounts=mounts,
                        security_context=client.V1SecurityContext(
                            allow_privilege_escalation=False,
                            capabilities=client.V1Capabilities(drop=["ALL"]),
                        )
                        if spec.runtime_class is None
                        else None,
                    )
                ],
                volumes=volumes,
            ),
        )
        try:
            self.core.create_namespaced_pod(spec.namespace, pod)
        except client.ApiException as exc:
            if not _is_conflict(exc):
                raise

    def _create_service_sync(self, spec: InstancePodSpec) -> None:
        svc = client.V1Service(
            metadata=client.V1ObjectMeta(
                name=spec.name,
                namespace=spec.namespace,
                labels={INSTANCE_LABEL: spec.name, MANAGED_LABEL: "true"},
            ),
            spec=client.V1ServiceSpec(
                type="NodePort",
                selector={INSTANCE_LABEL: spec.name},
                ports=[
                    client.V1ServicePort(
                        name="ssh", port=22, target_port=22, node_port=spec.ssh_node_port
                    ),
                    client.V1ServicePort(name="jupyter", port=8888, target_port=8888),
                ],
            ),
        )
        try:
            self.core.create_namespaced_service(spec.namespace, svc)
        except client.ApiException as exc:
            if not _is_conflict(exc):
                raise

    def _create_ingress_sync(self, spec: InstancePodSpec) -> None:
        ingress = client.V1Ingress(
            metadata=client.V1ObjectMeta(
                name=spec.name,
                namespace=spec.namespace,
                labels={INSTANCE_LABEL: spec.name, MANAGED_LABEL: "true"},
            ),
            spec=client.V1IngressSpec(
                rules=[
                    client.V1IngressRule(
                        host=spec.jupyter_host,
                        http=client.V1HTTPIngressRuleValue(
                            paths=[
                                client.V1HTTPIngressPath(
                                    path="/",
                                    path_type="Prefix",
                                    backend=client.V1IngressBackend(
                                        service=client.V1IngressServiceBackend(
                                            name=spec.name,
                                            port=client.V1ServiceBackendPort(number=8888),
                                        )
                                    ),
                                )
                            ]
                        ),
                    )
                ]
            ),
        )
        try:
            self.net.create_namespaced_ingress(spec.namespace, ingress)
        except client.ApiException as exc:
            if not _is_conflict(exc):
                raise

    async def delete_instance(self, namespace: str, name: str) -> None:
        await asyncio.to_thread(self._delete_instance_sync, namespace, name)

    def _delete_instance_sync(self, namespace: str, name: str) -> None:
        for deleter in (
            lambda: self.core.delete_namespaced_pod(name, namespace),
            lambda: self.core.delete_namespaced_service(name, namespace),
            lambda: self.net.delete_namespaced_ingress(name, namespace),
        ):
            try:
                deleter()
            except client.ApiException as exc:
                if not _is_not_found(exc):
                    raise

    async def get_status(self, namespace: str, name: str) -> PodStatus:
        return await asyncio.to_thread(self._get_status_sync, namespace, name)

    def _get_status_sync(self, namespace: str, name: str) -> PodStatus:
        try:
            pod: Any = self.core.read_namespaced_pod(name, namespace)
        except client.ApiException as exc:
            if _is_not_found(exc):
                return PodStatus(exists=False)
            raise
        conditions = pod.status.conditions or []
        ready = any(c.type == "Ready" and c.status == "True" for c in conditions)
        return PodStatus(
            exists=True,
            ready=ready,
            phase=pod.status.phase or "Unknown",
            node_name=pod.spec.node_name,
        )

    async def list_instance_pods(self) -> list[tuple[str, str]]:
        return await asyncio.to_thread(self._list_instance_pods_sync)

    def _list_instance_pods_sync(self) -> list[tuple[str, str]]:
        pods: Any = self.core.list_pod_for_all_namespaces(label_selector=MANAGED_LABEL)
        prefix = self.settings.k8s_namespace_prefix
        return [
            (p.metadata.namespace, p.metadata.name)
            for p in pods.items
            if p.metadata.namespace.startswith(prefix)
        ]

    async def available_gpus(self, pool_label: str) -> int:
        return await asyncio.to_thread(self._available_gpus_sync, pool_label)

    def _available_gpus_sync(self, pool_label: str) -> int:
        nodes: Any = self.core.list_node(label_selector=f"{POOL_NODE_LABEL}={pool_label}")
        total = 0
        for node in nodes.items:
            allocatable = node.status.allocatable or {}
            total += int(allocatable.get("nvidia.com/gpu", "0"))
        pods: Any = self.core.list_pod_for_all_namespaces(
            label_selector=MANAGED_LABEL, field_selector="status.phase!=Failed"
        )
        used = 0
        for pod in pods.items:
            if (pod.spec.node_selector or {}).get(POOL_NODE_LABEL) != pool_label:
                continue
            for c in pod.spec.containers:
                limits = (c.resources and c.resources.limits) or {}
                used += int(limits.get("nvidia.com/gpu", "0"))
        return max(0, total - used)

    async def list_nodes(self) -> list[NodeInfo]:
        return await asyncio.to_thread(self._list_nodes_sync)

    def _list_nodes_sync(self) -> list[NodeInfo]:
        nodes: Any = self.core.list_node(label_selector=POOL_NODE_LABEL)
        out: list[NodeInfo] = []
        for node in nodes.items:
            labels = node.metadata.labels or {}
            allocatable = node.status.allocatable or {}
            conditions = node.status.conditions or []
            ready = any(c.type == "Ready" and c.status == "True" for c in conditions)
            cordoned = bool(node.spec.unschedulable)
            pool = labels.get(POOL_NODE_LABEL, "unknown")
            total = int(allocatable.get("nvidia.com/gpu", "0"))
            out.append(
                NodeInfo(
                    name=node.metadata.name,
                    pool_label=pool,
                    gpu_model=labels.get("nvidia.com/gpu.product", "GPU"),
                    gpu_total=total,
                    gpu_used=max(0, total - self._available_gpus_sync(pool)),
                    status="Cordoned" if cordoned else ("Ready" if ready else "NotReady"),
                )
            )
        return out
