"""生产 K8s 编排(kubernetes 官方客户端 36.x,已对齐 K8s 1.36)。

官方客户端为同步实现,全部调用经 asyncio.to_thread 出让事件循环。
真实 GPU 行为(HAMi 限额/Kata 直通)只能实机验证。

对象命名:pod/svc/ingress 同名 = instance uuid;统一打标 superdl.io/instance。
"""

# pragma: no cover - 本文件需真实集群,单测不覆盖

import asyncio
import hashlib
from typing import Any, cast

from kubernetes import client, config

from app.core.config import get_settings
from app.core.k8s.base import (
    GPU_MODEL_NODE_LABEL,
    INSTANCE_DISK_STORAGE_CLASS,
    JUICEFS_PVC_NAME,
    JUICEFS_STORAGE_CLASS,
    ClusterProbe,
    InstancePodSpec,
    NodeInfo,
    PodStatus,
    PrewarmJobStatus,
    derive_distro,
    instance_disk_pvc_name,
)

INSTANCE_LABEL = "superdl.io/instance"
MANAGED_LABEL = "superdl.io/managed"
POOL_NODE_LABEL = "superdl.io/pool"
PREWARM_LABEL = "superdl.io/prewarm"  # 预热 Job 专用标签,与 managed(实例 Pod 查询)隔离
INGRESS_NAMESPACE = "ingress-nginx"  # Jupyter 北向入口所在 ns(NetworkPolicy 放行来源)
PLATFORM_NAMESPACE = "superdl"  # 平台自身 ns(deploy/app/k8s/00-namespace-config.yaml),预热 Job 落此


def _is_not_found(exc: client.ApiException) -> bool:
    return exc.status == 404


def _is_conflict(exc: client.ApiException) -> bool:
    return exc.status == 409


# 租户命名空间兜底配额(每用户配额主闸在 create_instance)
TENANT_QUOTA = {"pods": "64", "services": "64", "persistentvolumeclaims": "128"}
# 租户容器禁访的内网/元数据网段(Egress 白名单公网,黑名单私网)
PRIVATE_CIDRS = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"]


class _TimeoutApi:
    """给官方同步客户端的每次调用注入 `_request_timeout`(客户端无全局超时配置项)。

    不注入则 API server 挂起时 to_thread 的线程永久悬挂:outbox 单队列会被一个卡死的
    调用整队拖停,市场页库存查询在缓存过期后同样被挂住。包一层比在 40 余处调用点各写
    一遍可靠 —— 新增调用不会漏。
    """

    def __init__(self, api: Any, timeout: tuple[float, float]) -> None:
        self._api = api
        self._timeout = timeout

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._api, name)
        if not callable(attr):
            return attr

        def call(*args: Any, **kwargs: Any) -> Any:
            kwargs.setdefault("_request_timeout", self._timeout)
            return attr(*args, **kwargs)

        return call


class RealOrchestrator:
    def __init__(self) -> None:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        self.settings = get_settings()
        timeout = (
            self.settings.k8s_connect_timeout_seconds,
            self.settings.k8s_read_timeout_seconds,
        )
        # cast 保留静态签名检查,运行时是注超时的代理(见 _TimeoutApi)
        self.core = cast(client.CoreV1Api, _TimeoutApi(client.CoreV1Api(), timeout))
        self.net = cast(client.NetworkingV1Api, _TimeoutApi(client.NetworkingV1Api(), timeout))
        self.batch = cast(client.BatchV1Api, _TimeoutApi(client.BatchV1Api(), timeout))

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
        """入方向:默认拒东西向,仅放行 Ingress Controller 到 Jupyter 端口(北向入口);
        出方向放行公网 + DNS,禁访节点/Service/Pod 网段与云元数据(169.254.0.0/16)。

        SSH 走 NodePort(kube-proxy DNAT,不过 NetworkPolicy);Jupyter 走 Ingress,
        不显式放行则 ingress-nginx 连租户 Pod 被拒。
        """
        policy = client.V1NetworkPolicy(
            metadata=client.V1ObjectMeta(name="tenant-default", namespace=namespace),
            spec=client.V1NetworkPolicySpec(
                pod_selector=client.V1LabelSelector(),
                policy_types=["Ingress", "Egress"],
                ingress=[
                    # 北向:Ingress Controller → JupyterLab(8888)。其余东西向一律拒绝。
                    client.V1NetworkPolicyIngressRule(
                        _from=[
                            client.V1NetworkPolicyPeer(
                                namespace_selector=client.V1LabelSelector(
                                    match_labels={"kubernetes.io/metadata.name": INGRESS_NAMESPACE}
                                )
                            )
                        ],
                        ports=[client.V1NetworkPolicyPort(protocol="TCP", port=8888)],
                    )
                ],
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
        self._ensure_instance_disk_sync(spec)
        self._create_pod_sync(spec)
        self._create_service_sync(spec)
        self._create_ingress_sync(spec)

    def _ensure_instance_disk_sync(self, spec: InstancePodSpec) -> None:
        """实例盘 PVC。已存在即跳过 —— 重新开机必须复用同一只盘(用户的 conda 环境、
        代码、checkpoint 都在里面),绝不按新容量重建。TopoLVM 是节点本地卷,
        PV 带 node affinity,首次绑定后调度器会自动把后续 Pod 拉回原节点。"""
        pvc = client.V1PersistentVolumeClaim(
            metadata=client.V1ObjectMeta(
                name=instance_disk_pvc_name(spec.name),
                namespace=spec.namespace,
                labels={INSTANCE_LABEL: spec.name, MANAGED_LABEL: "true"},
            ),
            spec=client.V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteOnce"],
                storage_class_name=INSTANCE_DISK_STORAGE_CLASS,
                resources=client.V1VolumeResourceRequirements(
                    requests={"storage": f"{spec.disk_gb}Gi"}
                ),
            ),
        )
        try:
            self.core.create_namespaced_persistent_volume_claim(spec.namespace, pvc)
        except client.ApiException as exc:
            if not _is_conflict(exc):
                raise

    def _create_pod_sync(self, spec: InstancePodSpec) -> None:
        resources = {"cpu": str(spec.vcpu), "memory": f"{spec.mem_gb}Gi", **spec.gpu_resources}
        env = [client.V1EnvVar(name=k, value=v) for k, v in spec.env.items()]
        env.append(client.V1EnvVar(name="AUTHORIZED_KEYS", value="\n".join(spec.authorized_keys)))
        volumes: list[client.V1Volume] = [
            client.V1Volume(
                name="instance-disk",
                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                    claim_name=instance_disk_pvc_name(spec.name)
                ),
            )
        ]
        mounts = [client.V1VolumeMount(name="instance-disk", mount_path="/root")]
        if spec.data_disk_subpath:
            volumes.append(
                client.V1Volume(
                    name="data-disk",
                    persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                        claim_name=JUICEFS_PVC_NAME
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
                annotations=spec.annotations or None,
            ),
            spec=client.V1PodSpec(
                runtime_class_name=spec.runtime_class,
                scheduler_name=spec.scheduler_name,  # HAMi 池 = hami-scheduler(不赖 webhook)
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
            # 409 不一定是幂等命中。同名对象正在优雅删除(Terminating)时也是 409,
            # 把它当「已存在,跳过」意味着新 Pod 根本没被创建,而 handler 正常返回、
            # 任务标 done —— 没有重试、没有死信、没有告警,实例静默地再也起不来。
            existing: Any = self.core.read_namespaced_pod(spec.name, spec.namespace)
            if existing.metadata.deletion_timestamp is not None:
                raise RuntimeError(
                    f"pod {spec.name} is terminating; create must wait for it to disappear"
                ) from exc

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
                # 显式 IngressClass:IngressClass 未标 default 时,不写这行则无控制器接管
                ingress_class_name=self.settings.ingress_class_name,
                # TLS 不指定 secretName,由 ingress-nginx default-ssl-certificate
                # 提供 *.app 泛域名证书
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

    async def delete_instance(self, namespace: str, name: str, *, force: bool = False) -> None:
        await asyncio.to_thread(self._delete_instance_sync, namespace, name, force)

    def _delete_instance_sync(self, namespace: str, name: str, force: bool = False) -> None:
        # 节点失联时 kubelet 确认不了删除,Pod 会无限期 Terminating —— 强删(grace 0)
        # 直接从 etcd 摘掉对象,否则实例永远卡在 stopping/releasing 等一个不会到的确认。
        pod_kwargs = {"grace_period_seconds": 0} if force else {}
        for deleter in (
            lambda: self.core.delete_namespaced_pod(name, namespace, **pod_kwargs),
            lambda: self.core.delete_namespaced_service(name, namespace),
            lambda: self.net.delete_namespaced_ingress(name, namespace),
        ):
            try:
                deleter()
            except client.ApiException as exc:
                if not _is_not_found(exc):
                    raise

    async def delete_instance_disk(self, namespace: str, name: str) -> None:
        await asyncio.to_thread(self._delete_instance_disk_sync, namespace, name)

    def _delete_instance_disk_sync(self, namespace: str, name: str) -> None:
        try:
            self.core.delete_namespaced_persistent_volume_claim(
                instance_disk_pvc_name(name), namespace
            )
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
            deleting=pod.metadata.deletion_timestamp is not None,
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

    async def list_nodes(self, include_unlabeled: bool = False) -> list[NodeInfo]:
        return await asyncio.to_thread(self._list_nodes_sync, include_unlabeled)

    async def set_node_labels(self, node_name: str, labels: dict[str, str]) -> None:
        await asyncio.to_thread(self.core.patch_node, node_name, {"metadata": {"labels": labels}})

    @staticmethod
    def _qty_to_bytes(q: str | None) -> int:
        """K8s 资源量(如 49192080Ki / 200Gi / 500M)转字节。无法解析返回 0。"""
        if not q:
            return 0
        units = {
            "Ki": 1024,
            "Mi": 1024**2,
            "Gi": 1024**3,
            "Ti": 1024**4,
            "Pi": 1024**5,
            "K": 1000,
            "M": 1000**2,
            "G": 1000**3,
            "T": 1000**4,
            "P": 1000**5,
        }
        for suf, mult in units.items():
            if q.endswith(suf):
                try:
                    return int(float(q[: -len(suf)]) * mult)
                except ValueError:
                    return 0
        try:
            return int(float(q))
        except ValueError:
            return 0

    @staticmethod
    def _cpu_cores(q: str | None) -> int:
        """CPU 量(核数 "4" 或毫核 "3920m")转整核。"""
        if not q:
            return 0
        try:
            return max(1, round(int(q[:-1]) / 1000)) if q.endswith("m") else int(float(q))
        except ValueError:
            return 0

    def _list_nodes_sync(self, include_unlabeled: bool = False) -> list[NodeInfo]:
        selector = None if include_unlabeled else POOL_NODE_LABEL
        nodes: Any = self.core.list_node(label_selector=selector)
        used_by_node = self._used_gpus_by_node()  # 一次拉取全量,避免逐节点扫 Pod
        out: list[NodeInfo] = []
        for node in nodes.items:
            labels = node.metadata.labels or {}
            conditions = node.status.conditions or []
            ready = any(c.type == "Ready" and c.status == "True" for c in conditions)
            cordoned = bool(node.spec.unschedulable)
            total = self._gpu_amount(node.status.allocatable)
            cap = node.status.capacity or {}
            out.append(
                NodeInfo(
                    name=node.metadata.name,
                    pool_label=labels.get(POOL_NODE_LABEL, "unknown"),
                    gpu_model=labels.get("nvidia.com/gpu.product", "GPU"),
                    gpu_total=total,
                    gpu_used=min(total, used_by_node.get(node.metadata.name, 0)),
                    status="Cordoned" if cordoned else ("Ready" if ready else "NotReady"),
                    vcpu=self._cpu_cores(cap.get("cpu")),
                    mem_gb=self._qty_to_bytes(cap.get("memory")) // 1024**3,
                    disk_gb=self._qty_to_bytes(cap.get("ephemeral-storage")) // 1024**3,
                    gpu_model_label=labels.get("nvidia.com/gpu.product", ""),
                    model_label_current=labels.get(GPU_MODEL_NODE_LABEL, ""),
                )
            )
        return out

    async def probe_cluster(self) -> ClusterProbe:
        return await asyncio.to_thread(self._probe_cluster_sync)

    def _probe_cluster_sync(self) -> ClusterProbe:
        # 版本失败 = API 不可达,整体判不可用;组件清点逐项容错(RBAC 缺项不清零全局)
        try:
            version: Any = client.VersionApi().get_code()
            git_version = getattr(version, "git_version", None)
        except Exception as exc:
            return ClusterProbe(api_reachable=False, error=str(exc))
        errors: list[str] = []
        apps = client.AppsV1Api()
        hami_ready = dcgm = kps = gpu_operator = False
        try:
            deployments: Any = apps.list_deployment_for_all_namespaces()
            for d in deployments.items:
                name = d.metadata.name or ""
                if name == "hami-scheduler":
                    hami_ready = bool(d.status.ready_replicas)
                if "gpu-operator" in name:
                    gpu_operator = True
                if "kube-prometheus-stack" in name:
                    kps = True
            daemonsets: Any = apps.list_daemon_set_for_all_namespaces()
            for ds in daemonsets.items:
                if "dcgm" in (ds.metadata.name or ""):
                    dcgm = True
            if not kps:
                statefulsets: Any = apps.list_stateful_set_for_all_namespaces()
                kps = any(
                    (st.metadata.name or "").startswith("prometheus-") for st in statefulsets.items
                )
        except client.ApiException as exc:
            errors.append(f"apps: {exc.status}")
        runtime_classes: tuple[str, ...] = ()
        try:
            rcs: Any = client.NodeV1Api().list_runtime_class()
            runtime_classes = tuple(rc.metadata.name for rc in rcs.items)
        except client.ApiException as exc:
            errors.append(f"runtimeclasses: {exc.status}")
        storage_classes: tuple[str, ...] = ()
        try:
            scs: Any = client.StorageV1Api().list_storage_class()
            storage_classes = tuple(sc.metadata.name for sc in scs.items)
        except client.ApiException as exc:
            errors.append(f"storageclasses: {exc.status}")
        pools: dict[str, int] = {}
        try:
            for node in self._list_nodes_sync(True):
                key = node.pool_label if node.pool_label != "unknown" else "unlabeled"
                pools[key] = pools.get(key, 0) + 1
        except client.ApiException as exc:
            errors.append(f"nodes: {exc.status}")
        return ClusterProbe(
            api_reachable=True,
            k8s_version=git_version,
            distro=derive_distro(git_version),
            hami_ready=hami_ready,
            dcgm_present=dcgm,
            kps_present=kps,
            gpu_operator_present=gpu_operator,
            kata_runtimeclass="kata-qemu" in runtime_classes,
            storage_classes=storage_classes,
            runtime_classes=runtime_classes,
            pools=pools,
            error="; ".join(errors) or None,
        )

    async def set_node_unschedulable(self, node_name: str, unschedulable: bool) -> None:
        await asyncio.to_thread(self._set_node_unschedulable_sync, node_name, unschedulable)

    def _set_node_unschedulable_sync(self, node_name: str, unschedulable: bool) -> None:
        # RBAC:需 ClusterRole nodes patch(deploy/app/k8s/01-rbac.yaml)
        self.core.patch_node(node_name, {"spec": {"unschedulable": unschedulable}})

    # ---------- 镜像预热 ----------

    @staticmethod
    def _prewarm_job_name(node_name: str, image_ref: str) -> str:
        """确定性命名(≤63 字符):同(节点,镜像)天然幂等。"""
        ref_hash = hashlib.sha1(image_ref.encode()).hexdigest()[:10]
        node_hash = hashlib.sha1(node_name.encode()).hexdigest()[:8]
        return f"prewarm-{ref_hash}-{node_hash}"

    async def prewarm_image(self, node_name: str, image_ref: str) -> None:
        await asyncio.to_thread(self._prewarm_image_sync, node_name, image_ref)

    def _prewarm_image_sync(self, node_name: str, image_ref: str) -> None:
        """nodeName 定点起拉取 Job,创建后即返回(不等待,大镜像拉取可达数十分钟,
        完成态由 prewarm_patrol 巡检经 get_prewarm_status 收敛)。已存在同名 Job 则跳过。"""
        job_name = self._prewarm_job_name(node_name, image_ref)
        try:
            self.batch.read_namespaced_job(job_name, PLATFORM_NAMESPACE)
            return  # 幂等:任意状态的既有 Job 都交巡检收敛
        except client.ApiException as exc:
            if not _is_not_found(exc):
                raise
        job = client.V1Job(
            metadata=client.V1ObjectMeta(
                name=job_name,
                namespace=PLATFORM_NAMESPACE,
                labels={PREWARM_LABEL: "true"},
                annotations={"superdl.io/node": node_name, "superdl.io/image": image_ref},
            ),
            spec=client.V1JobSpec(
                backoff_limit=0,  # 失败不原地重试,由巡检删 Job 后重建(带退避节流)
                ttl_seconds_after_finished=600,
                active_deadline_seconds=1800,  # 20GB 级镜像上限,实机核定
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(labels={PREWARM_LABEL: "true"}),
                    spec=client.V1PodSpec(
                        node_name=node_name,  # 绕过调度器定点拉取
                        restart_policy="Never",
                        automount_service_account_token=False,
                        # 容忍一切污点:预热须覆盖 cordon/维护中的节点
                        tolerations=[client.V1Toleration(operator="Exists")],
                        containers=[
                            client.V1Container(
                                name="prewarm",
                                image=image_ref,
                                # 平台镜像均含 sh;缺 sh 会 StartError,由巡检记 failed
                                command=["/bin/sh", "-c", "true"],
                                image_pull_policy="IfNotPresent",
                                resources=client.V1ResourceRequirements(
                                    requests={"cpu": "10m", "memory": "16Mi"},
                                    limits={"cpu": "100m", "memory": "64Mi"},
                                ),
                                security_context=client.V1SecurityContext(
                                    allow_privilege_escalation=False,
                                    capabilities=client.V1Capabilities(drop=["ALL"]),
                                ),
                            )
                        ],
                    ),
                ),
            ),
        )
        try:
            self.batch.create_namespaced_job(PLATFORM_NAMESPACE, job)
        except client.ApiException as exc:
            if not _is_conflict(exc):
                raise

    async def get_prewarm_status(self, node_name: str, image_ref: str) -> PrewarmJobStatus:
        return await asyncio.to_thread(self._get_prewarm_status_sync, node_name, image_ref)

    def _get_prewarm_status_sync(self, node_name: str, image_ref: str) -> PrewarmJobStatus:
        job_name = self._prewarm_job_name(node_name, image_ref)
        try:
            job: Any = self.batch.read_namespaced_job(job_name, PLATFORM_NAMESPACE)
        except client.ApiException as exc:
            if _is_not_found(exc):
                return PrewarmJobStatus(state="absent")
            raise
        if (job.status.succeeded or 0) >= 1:
            return PrewarmJobStatus(state="succeeded")
        if (job.status.failed or 0) >= 1:
            return PrewarmJobStatus(state="failed", message=self._prewarm_failure_sync(job_name))
        return PrewarmJobStatus(state="running")

    def _prewarm_failure_sync(self, job_name: str) -> str:
        """失败原因优先取 Pod 容器态(ErrImagePull 等),兜底 Job condition。"""
        try:
            pods: Any = self.core.list_namespaced_pod(
                PLATFORM_NAMESPACE, label_selector=f"job-name={job_name}"
            )
            for pod in pods.items:
                for cs in pod.status.container_statuses or []:
                    waiting = cs.state and cs.state.waiting
                    if waiting and waiting.reason:
                        return f"{waiting.reason}: {waiting.message or ''}"[:500]
                    terminated = cs.state and cs.state.terminated
                    if terminated and terminated.reason and terminated.reason != "Completed":
                        return f"{terminated.reason}: {terminated.message or ''}"[:500]
        except client.ApiException:
            pass
        return "job failed (BackoffLimitExceeded/DeadlineExceeded)"

    async def delete_prewarm_job(self, node_name: str, image_ref: str) -> None:
        await asyncio.to_thread(self._delete_prewarm_job_sync, node_name, image_ref)

    def _delete_prewarm_job_sync(self, node_name: str, image_ref: str) -> None:
        job_name = self._prewarm_job_name(node_name, image_ref)
        try:
            self.batch.delete_namespaced_job(
                job_name, PLATFORM_NAMESPACE, propagation_policy="Background"
            )
        except client.ApiException as exc:
            if not _is_not_found(exc):
                raise
