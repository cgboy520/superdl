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


# 租户命名空间兜底配额(应用层 create_instance 的每用户配额是主闸;这里防绕过与失控 Pod)
TENANT_QUOTA = {"pods": "64", "services": "64", "persistentvolumeclaims": "128"}
# 租户容器禁访的内网/元数据网段(Egress 白名单公网,黑名单私网)
PRIVATE_CIDRS = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"]
JUICEFS_PVC_NAME = "juicefs-shared"
JUICEFS_STORAGE_CLASS = "juicefs-sc"


class RealOrchestrator:
    def __init__(self) -> None:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        self.core = client.CoreV1Api()
        self.net = client.NetworkingV1Api()
        self.batch = client.BatchV1Api()
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
        self._ensure_quota_sync(namespace)
        self._ensure_juicefs_pvc_sync(namespace)

    def _ensure_default_netpol_sync(self, namespace: str) -> None:
        """入方向默认拒(北向经 LB/Ingress);出方向放行公网 + DNS,
        禁访节点/Service/Pod 网段与云元数据(169.254.0.0/16)。"""
        policy = client.V1NetworkPolicy(
            metadata=client.V1ObjectMeta(name="tenant-default", namespace=namespace),
            spec=client.V1NetworkPolicySpec(
                pod_selector=client.V1LabelSelector(),
                policy_types=["Ingress", "Egress"],
                ingress=[],  # 拒全部东西向入方向
                egress=[
                    # DNS(kube-system CoreDNS)
                    client.V1NetworkPolicyEgressRule(
                        to=[
                            client.V1NetworkPolicyPeer(
                                namespace_selector=client.V1LabelSelector(
                                    match_labels={"kubernetes.io/metadata.name": "kube-system"}
                                )
                            )
                        ],
                        ports=[
                            client.V1NetworkPolicyPort(protocol="UDP", port=53),
                            client.V1NetworkPolicyPort(protocol="TCP", port=53),
                        ],
                    ),
                    # 公网:0.0.0.0/0 除私网与元数据段
                    client.V1NetworkPolicyEgressRule(
                        to=[
                            client.V1NetworkPolicyPeer(
                                ip_block=client.V1IPBlock(cidr="0.0.0.0/0", _except=PRIVATE_CIDRS)
                            )
                        ]
                    ),
                ],
            ),
        )
        try:
            self.net.create_namespaced_network_policy(namespace, policy)
        except client.ApiException as exc:
            if not _is_conflict(exc):
                raise

    def _ensure_quota_sync(self, namespace: str) -> None:
        quota = client.V1ResourceQuota(
            metadata=client.V1ObjectMeta(name="tenant-quota", namespace=namespace),
            spec=client.V1ResourceQuotaSpec(hard=dict(TENANT_QUOTA)),
        )
        try:
            self.core.create_namespaced_resource_quota(namespace, quota)
        except client.ApiException as exc:
            if not _is_conflict(exc):
                raise

    def _ensure_juicefs_pvc_sync(self, namespace: str) -> None:
        """每租户 namespace 一只共享 JuiceFS PVC(数据盘 subPath 挂载的底座)。
        容量是名义值 —— 真实额度由 JuiceFS 目录配额管。"""
        pvc = client.V1PersistentVolumeClaim(
            metadata=client.V1ObjectMeta(
                name=JUICEFS_PVC_NAME, namespace=namespace, labels={MANAGED_LABEL: "true"}
            ),
            spec=client.V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteMany"],
                storage_class_name=JUICEFS_STORAGE_CLASS,
                resources=client.V1VolumeResourceRequirements(requests={"storage": "10Ti"}),
            ),
        )
        try:
            self.core.create_namespaced_persistent_volume_claim(namespace, pvc)
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
                # 仅共享池显式收紧(hostUsers: false 开 userns);独享 Kata 走默认
                host_users=False if spec.host_users is False else None,
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
                # TLS:不指定 secretName,由 ingress-nginx default-ssl-certificate
                # 提供 *.app 泛域名证书(证书 Secret 无需复制进每个租户 ns)
                tls=[client.V1IngressTLS(hosts=[spec.jupyter_host])],
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
                ],
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

    # ---------- 数据盘擦除 ----------

    async def wipe_disk(self, namespace: str, subpath: str) -> None:
        await asyncio.to_thread(self._wipe_disk_sync, namespace, subpath)

    def _wipe_disk_sync(self, namespace: str, subpath: str) -> None:
        """租户 ns 内起 Job 挂 JuiceFS PVC 删除子目录。幂等:
        Job 已成功 → 清理并返回;进行中 → 抛错交 outbox 退避重试;失败 → 删 Job 重建。"""
        if "/" in subpath or ".." in subpath or not subpath:
            raise ValueError(f"illegal juicefs subpath: {subpath!r}")
        job_name = f"wipe-{subpath[-40:]}".lower()
        try:
            existing: Any = self.batch.read_namespaced_job(job_name, namespace)
        except client.ApiException as exc:
            if not _is_not_found(exc):
                raise
            existing = None
        if existing is not None:
            if (existing.status.succeeded or 0) >= 1:
                self.batch.delete_namespaced_job(
                    job_name, namespace, propagation_policy="Background"
                )
                return
            if (existing.status.failed or 0) >= 1:
                self.batch.delete_namespaced_job(
                    job_name, namespace, propagation_policy="Background"
                )
                raise RuntimeError(f"disk wipe job failed, recreated next retry: {job_name}")
            raise RuntimeError(f"disk wipe job still running: {job_name}")
        job = client.V1Job(
            metadata=client.V1ObjectMeta(
                name=job_name, namespace=namespace, labels={MANAGED_LABEL: "true"}
            ),
            spec=client.V1JobSpec(
                backoff_limit=1,
                ttl_seconds_after_finished=3600,
                template=client.V1PodTemplateSpec(
                    spec=client.V1PodSpec(
                        restart_policy="Never",
                        automount_service_account_token=False,
                        containers=[
                            client.V1Container(
                                name="wipe",
                                image="busybox:1.36",
                                command=["rm", "-rf", f"/data/{subpath}"],
                                volume_mounts=[
                                    client.V1VolumeMount(name="juicefs", mount_path="/data")
                                ],
                                security_context=client.V1SecurityContext(
                                    allow_privilege_escalation=False,
                                    capabilities=client.V1Capabilities(drop=["ALL"]),
                                ),
                            )
                        ],
                        volumes=[
                            client.V1Volume(
                                name="juicefs",
                                persistent_volume_claim=(
                                    client.V1PersistentVolumeClaimVolumeSource(
                                        claim_name=JUICEFS_PVC_NAME
                                    )
                                ),
                            )
                        ],
                    )
                ),
            ),
        )
        try:
            self.batch.create_namespaced_job(namespace, job)
        except client.ApiException as exc:
            if not _is_conflict(exc):
                raise
        raise RuntimeError(f"disk wipe job created, awaiting completion: {job_name}")

    # ---------- 库存与节点 ----------

    @staticmethod
    def _gpu_amount(resources: dict[str, Any] | None) -> int:
        """整卡 + MIG 分片统一计数(HAMi 共享池的 nvidia.com/gpu 为虚拟化后份额)。"""
        total = 0
        for key, value in (resources or {}).items():
            if key == "nvidia.com/gpu" or key.startswith("nvidia.com/mig-"):
                total += int(value)
        return total

    async def available_gpus(self, pool_label: str) -> int:
        return await asyncio.to_thread(self._available_gpus_sync, pool_label)

    def _available_gpus_sync(self, pool_label: str) -> int:
        nodes: Any = self.core.list_node(label_selector=f"{POOL_NODE_LABEL}={pool_label}")
        total = sum(self._gpu_amount(n.status.allocatable) for n in nodes.items)
        used = self._used_gpus_by_pool().get(pool_label, 0)
        return max(0, total - used)

    def _used_gpus_by_pool(self) -> dict[str, int]:
        """全部受管 Pod 一次拉取,按池聚合已用份额(整卡 + MIG + HAMi 虚拟份额)。"""
        pods: Any = self.core.list_pod_for_all_namespaces(
            label_selector=MANAGED_LABEL, field_selector="status.phase!=Failed"
        )
        used: dict[str, int] = {}
        for pod in pods.items:
            pool = (pod.spec.node_selector or {}).get(POOL_NODE_LABEL)
            if pool is None:
                continue
            for c in pod.spec.containers:
                limits = (c.resources and c.resources.limits) or {}
                used[pool] = used.get(pool, 0) + self._gpu_amount(limits)
        return used

    def _used_gpus_by_node(self) -> dict[str, int]:
        pods: Any = self.core.list_pod_for_all_namespaces(
            label_selector=MANAGED_LABEL, field_selector="status.phase!=Failed"
        )
        used: dict[str, int] = {}
        for pod in pods.items:
            node = pod.spec.node_name
            if not node:
                continue
            for c in pod.spec.containers:
                limits = (c.resources and c.resources.limits) or {}
                used[node] = used.get(node, 0) + self._gpu_amount(limits)
        return used

    async def list_nodes(self) -> list[NodeInfo]:
        return await asyncio.to_thread(self._list_nodes_sync)

    def _list_nodes_sync(self) -> list[NodeInfo]:
        nodes: Any = self.core.list_node(label_selector=POOL_NODE_LABEL)
        used_by_node = self._used_gpus_by_node()  # 一次拉取,不再每节点全量扫 Pod
        out: list[NodeInfo] = []
        for node in nodes.items:
            labels = node.metadata.labels or {}
            conditions = node.status.conditions or []
            ready = any(c.type == "Ready" and c.status == "True" for c in conditions)
            cordoned = bool(node.spec.unschedulable)
            total = self._gpu_amount(node.status.allocatable)
            out.append(
                NodeInfo(
                    name=node.metadata.name,
                    pool_label=labels.get(POOL_NODE_LABEL, "unknown"),
                    gpu_model=labels.get("nvidia.com/gpu.product", "GPU"),
                    gpu_total=total,
                    gpu_used=min(total, used_by_node.get(node.metadata.name, 0)),
                    status="Cordoned" if cordoned else ("Ready" if ready else "NotReady"),
                )
            )
        return out
