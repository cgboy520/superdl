"""生产 K8s 编排(kubernetes 官方同步客户端,全部调用经 _run 出让到专属执行器)。

对象命名:pod/svc/httproute 同名 = instance uuid;统一打标 superdl.io/instance。
"""

import asyncio
import hashlib
import ipaddress
import math
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast

from kubernetes import client, config

from app.core.config import get_settings
from app.core.k8s.base import (
    GATEWAY_API_GROUP,
    GATEWAY_API_VERSION,
    GATEWAY_APP_LISTENER,
    GATEWAY_NAME,
    GATEWAY_NAMESPACE,
    GATEWAY_PLURAL,
    GATEWAY_SVC_LISTENER,
    GPU_MODEL_NODE_LABEL,
    HTTPROUTE_PLURAL,
    INSTANCE_DISK_STORAGE_CLASS,
    JUICEFS_PVC_NAME,
    JUICEFS_STORAGE_CLASS,
    MANAGED_LABEL,
    POOL_NODE_LABEL,
    ClusterProbe,
    InstancePodSpec,
    NodeInfo,
    NodePortTaken,
    PodStatus,
    PrewarmJobStatus,
    derive_distro,
    instance_disk_pvc_name,
    instance_env_secret_name,
    jupyter_service_name,
    service_endpoint_service_name,
)
from app.core.logging import get_logger
from app.core.registry import PULL_SECRET_FINGERPRINT_ANNOTATION, PULL_SECRET_NAME

INSTANCE_LABEL = "superdl.io/instance"
PREWARM_LABEL = "superdl.io/prewarm"  # 预热 Job 专用标签
# Envoy 数据面 Pod 所在 ns(≠ base.GATEWAY_NAMESPACE),与 deploy/cluster/helmfile.yaml.gotmpl
# 的 envoy-gateway release namespace 一致
GATEWAY_DATAPLANE_NAMESPACE = "envoy-gateway-system"


def pod_cidr_gateways(cidrs: Iterable[str]) -> list[str]:
    """各节点 Pod 子网的 .0 与 .1 地址的 /32 列表(flannel 跨节点 NodePort 的 SNAT 来源),排序去重;
    IPv6 / 非法串忽略。"""
    out: set[str] = set()
    for raw in cidrs:
        try:
            net = ipaddress.ip_network(raw, strict=False)
        except ValueError:
            continue
        if net.version != 4:
            continue
        out.add(f"{net.network_address}/32")
        out.add(f"{net.network_address + 1}/32")
    return sorted(out, key=lambda c: ipaddress.ip_address(c.split("/")[0]))


# 租户 ns 的 PSA 标签:enforce baseline(平台镜像以 root 运行),audit/warn restricted
TENANT_NS_PSA_LABELS = {
    "pod-security.kubernetes.io/enforce": "baseline",
    "pod-security.kubernetes.io/audit": "restricted",
    "pod-security.kubernetes.io/warn": "restricted",
}


def tenant_security_context() -> "client.V1SecurityContext":
    """租户容器加固基线(无条件下发):不设 runAsNonRoot;drop ALL 后 add 回
    SYS_CHROOT/SETUID/SETGID(sshd 预认证特权分离所需)。见 docs/decisions.md。"""
    return client.V1SecurityContext(
        allow_privilege_escalation=False,
        capabilities=client.V1Capabilities(drop=["ALL"], add=["SYS_CHROOT", "SETUID", "SETGID"]),
        seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"),
    )


def platform_job_security_context() -> "client.V1SecurityContext":
    """superdl ns 一次性 Job(预热/配额)的 PSA restricted 上下文:非 root + 零 capability。
    擦除 Job 不用它(在租户 ns 以 root 运行)。"""
    return client.V1SecurityContext(
        run_as_non_root=True,
        run_as_user=65534,
        run_as_group=65534,
        allow_privilege_escalation=False,
        capabilities=client.V1Capabilities(drop=["ALL"]),
        seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"),
    )


def _is_conflict(exc: client.ApiException) -> bool:
    return exc.status == 409


def _ignore(fn: Callable[[], Any], *statuses: int) -> Any:
    """执行一次 K8s 调用,吞掉指定 HTTP 状态的 ApiException 并返回 None,其余照抛。"""
    try:
        return fn()
    except client.ApiException as exc:
        if exc.status not in statuses:
            raise
        return None


def _create_or_patch(create: Callable[[], Any], patch: Callable[[], Any]) -> None:
    """幂等下发:create 撞 409 即 patch 收敛存量对象。"""
    try:
        create()
    except client.ApiException as exc:
        if not _is_conflict(exc):
            raise
        patch()


def _ready_condition(obj: Any) -> bool:
    """Pod / Node 的 status.conditions 里 Ready 是否为 True。"""
    return any(c.type == "Ready" and c.status == "True" for c in (obj.status.conditions or []))


def _is_node_port_taken(exc: client.ApiException) -> bool:
    """apiserver 拒绝显式 nodePort 的形状:422 + "provided port is already allocated";
    同名 Service 幂等重放也会命中,占用者是否自己须读对象判。"""
    return exc.status == 422 and "already allocated" in str(exc.body or "")


# 租户 ns 兜底配额(主闸是每用户配额);含 cpu/memory/ephemeral 后 ns 内 Pod 必须声明 request/limit
TENANT_QUOTA = {
    "pods": "64",
    "services": "64",
    "persistentvolumeclaims": "128",
    "requests.cpu": "256",
    "requests.memory": "1Ti",
    "requests.ephemeral-storage": "500Gi",
    "limits.cpu": "256",
    "limits.memory": "1Ti",
    "limits.ephemeral-storage": "500Gi",
}
# LimitRange:给未声明 request/limit 的容器注默认值;PID 上限由 kubelet podPidsLimit 承担
TENANT_LIMIT_DEFAULT_REQUEST = {"cpu": "100m", "memory": "256Mi", "ephemeral-storage": "1Gi"}
TENANT_LIMIT_DEFAULT = {"cpu": "8", "memory": "32Gi", "ephemeral-storage": "64Gi"}
# 租户 ns 内授予 tenant-mgr 的 secrets Role/RoleBinding 名(与 deploy/app/k8s/01-rbac.yaml 同源)
TENANT_MGR_ROLE_NAME = "superdl-tenant-mgr-secrets"
TENANT_MGR_SA_NAME = "superdl-tenant-mgr"
# 租户 Egress 禁访的内网/元数据网段(100.64.0.0/10 = CGNAT,198.18.0.0/15 = 基准测试段);IPv6 不入表
PRIVATE_CIDRS = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "100.64.0.0/10",
    "198.18.0.0/15",
]
# Egress TCP 端口黑名单:SMTP(25/465/587)、SMB/NetBIOS(135/139/445)、Telnet(23)、RDP(3389)、
# MySQL/PG/Redis/ES/Memcached/MongoDB(3306/5432/6379/9200/11211/27017);例外走工单白名单
EGRESS_BLOCKED_TCP_PORTS = (
    23,
    25,
    135,
    139,
    445,
    465,
    587,
    3306,
    3389,
    5432,
    6379,
    9200,
    11211,
    27017,
)
# 公网 UDP 白名单:53 / 443(QUIC);其余走工单白名单
EGRESS_ALLOWED_UDP_PORTS = (53, 443)

# 数据盘擦除 Job 镜像:digest 钉死(与 scripts/release.sh 的清单镜像同规矩)
WIPE_IMAGE = "busybox:1.36@sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662"
# 租户容器 ephemeral-storage(可写层 + 日志 + emptyDir);超 limit 即驱逐。
# request 是调度器预留量:每节点超卖比 = limit/request,压到 6.4 倍(ns 配额 500Gi 同时约束总量)
TENANT_EPHEMERAL_REQUEST = "10Gi"
TENANT_EPHEMERAL_LIMIT = "64Gi"


def _container_ports(spec: InstancePodSpec) -> list["client.V1ContainerPort"]:
    """容器端口声明(Service targetPort 按名引用):dev 22+8888;service 用户端口(+ 开 SSH 时的 22)。"""
    ports: list[client.V1ContainerPort] = []
    if spec.with_ssh:
        ports.append(client.V1ContainerPort(container_port=22, name="ssh"))
    if spec.service_port is not None:
        ports.append(client.V1ContainerPort(container_port=spec.service_port, name="svc"))
    else:
        ports.append(client.V1ContainerPort(container_port=8888, name="jupyter"))
    return ports


def _health_probe(spec: InstancePodSpec, *, failure_threshold: int) -> "client.V1Probe | None":
    """health_path 非空时的 httpGet 探针(startup 与 readiness 同形状,只差阈值);dev 实例无探针。"""
    if not spec.health_path or spec.service_port is None:
        return None
    return client.V1Probe(
        http_get=client.V1HTTPGetAction(path=spec.health_path, port=spec.service_port),
        period_seconds=10,
        timeout_seconds=3,
        failure_threshold=failure_threshold,
    )


def _check_subpath(subpath: str) -> None:
    """JuiceFS 子路径只许单段目录名(拒绝 /、.. 与空串)。"""
    if "/" in subpath or ".." in subpath or not subpath:
        raise ValueError(f"illegal juicefs subpath: {subpath!r}")


def _allowed_tcp_port_ranges() -> list["client.V1NetworkPolicyPort"]:
    """1-65535 扣除黑名单端口后的允许区间(endPort)。"""
    ports: list[client.V1NetworkPolicyPort] = []
    lo = 1
    for blocked in sorted(EGRESS_BLOCKED_TCP_PORTS):
        if lo < blocked:
            ports.append(client.V1NetworkPolicyPort(protocol="TCP", port=lo, end_port=blocked - 1))
        lo = blocked + 1
    ports.append(client.V1NetworkPolicyPort(protocol="TCP", port=lo, end_port=65535))
    return ports


class _TimeoutApi:
    """给官方同步客户端的每次调用注入 `_request_timeout`。"""

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


logger = get_logger(__name__)


def build_instance_pod(spec: InstancePodSpec) -> "client.V1Pod":
    """实例 Pod 对象(纯构造,不触 API);CI 用它对准入策略做 dry-run 对账
    (scripts/render_admission_probes.py)。"""
    requests = {
        "cpu": str(spec.vcpu),
        "memory": f"{spec.mem_gb}Gi",
        "ephemeral-storage": TENANT_EPHEMERAL_REQUEST,
        **spec.gpu_resources,
    }
    limits = {**requests, "ephemeral-storage": TENANT_EPHEMERAL_LIMIT}
    env = [client.V1EnvVar(name=k, value=v) for k, v in spec.env.items()]
    # 敏感值以 secretKeyRef 引用 per-instance Secret
    secret_name = instance_env_secret_name(spec.name)
    for key in spec.secret_env:
        env.append(
            client.V1EnvVar(
                name=key,
                value_from=client.V1EnvVarSource(
                    secret_key_ref=client.V1SecretKeySelector(name=secret_name, key=key)
                ),
            )
        )
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
    return client.V1Pod(
        metadata=client.V1ObjectMeta(
            name=spec.name,
            namespace=spec.namespace,
            labels={INSTANCE_LABEL: spec.name, MANAGED_LABEL: "true"},
            annotations=spec.annotations or None,
        ),
        spec=client.V1PodSpec(
            runtime_class_name=spec.runtime_class,
            scheduler_name=spec.scheduler_name,  # HAMi 池 = hami-scheduler(不赖 webhook)
            # 共享池 hostUsers: false(userns);独享 Kata 走默认
            host_users=False if spec.host_users is False else None,
            # dev Never;service Always(kubelet 原地重启容器)
            restart_policy=spec.restart_policy,
            node_selector=spec.node_selector or None,
            termination_grace_period_seconds=30,
            automount_service_account_token=False,
            enable_service_links=False,
            # Harbor 拉取凭据(未配机器人则不引用)
            image_pull_secrets=(
                [client.V1LocalObjectReference(name=spec.image_pull_secret)]
                if spec.image_pull_secret
                else None
            ),
            containers=[
                client.V1Container(
                    name="workspace",
                    image=spec.image,
                    resources=client.V1ResourceRequirements(limits=limits, requests=requests),
                    env=env,
                    command=list(spec.command) if spec.command else None,
                    args=list(spec.args) if spec.args else None,
                    ports=_container_ports(spec),
                    volume_mounts=mounts,
                    security_context=tenant_security_context(),
                    startup_probe=_health_probe(spec, failure_threshold=90),
                    readiness_probe=_health_probe(spec, failure_threshold=3),
                )
            ],
            volumes=volumes,
        ),
    )


def build_managed_job(
    namespace: str,
    job_name: str,
    container: Any,
    volumes: list[Any],
    pod_labels: dict[str, str],
) -> "client.V1Job":
    """受管 Job 对象(wipe/quota 共用,纯构造)。hostUsers=false 显式写出:租户 ns 的准入策略
    (superdl-tenant-pod-baseline)要求非 Kata Pod 必须带它,缺了 Job 会被建出而 Pod 永远被拒。"""
    return client.V1Job(
        metadata=client.V1ObjectMeta(
            name=job_name,
            namespace=namespace,
            labels={MANAGED_LABEL: "true", **pod_labels},
        ),
        spec=client.V1JobSpec(
            backoff_limit=1,
            ttl_seconds_after_finished=3600,
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(labels={MANAGED_LABEL: "true", **pod_labels}),
                spec=client.V1PodSpec(
                    restart_policy="Never",
                    automount_service_account_token=False,
                    host_users=False,
                    containers=[container],
                    volumes=volumes,
                ),
            ),
        ),
    )


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
        self._timeout = timeout  # 供探测等裸客户端包装(见 _probe_cluster_sync)
        self.core = cast(client.CoreV1Api, _TimeoutApi(client.CoreV1Api(), timeout))
        self.net = cast(client.NetworkingV1Api, _TimeoutApi(client.NetworkingV1Api(), timeout))
        self.batch = cast(client.BatchV1Api, _TimeoutApi(client.BatchV1Api(), timeout))
        self.rbac = cast(
            client.RbacAuthorizationV1Api,
            _TimeoutApi(client.RbacAuthorizationV1Api(), timeout),
        )
        # Gateway API 无 typed model,HTTPRoute 走 CustomObjectsApi
        self.custom = cast(client.CustomObjectsApi, _TimeoutApi(client.CustomObjectsApi(), timeout))
        # 探测专用客户端共享同一个 ApiClient(单 PoolManager)
        probe_client = client.ApiClient()
        self._version = cast(
            client.VersionApi, _TimeoutApi(client.VersionApi(probe_client), timeout)
        )
        self._apps = cast(client.AppsV1Api, _TimeoutApi(client.AppsV1Api(probe_client), timeout))
        self._node = cast(client.NodeV1Api, _TimeoutApi(client.NodeV1Api(probe_client), timeout))
        self._storage = cast(
            client.StorageV1Api, _TimeoutApi(client.StorageV1Api(probe_client), timeout)
        )
        # K8s 同步调用走专属有界执行器,与默认执行器隔离
        self._executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="k8s")

    async def _run(self, fn: Any, *args: Any) -> Any:
        """asyncio.to_thread 等价物,换专属执行器。"""
        return await asyncio.get_running_loop().run_in_executor(self._executor, fn, *args)

    # ---------- namespace ----------

    async def ensure_namespace(self, namespace: str) -> None:
        await self._run(self._ensure_namespace_sync, namespace)

    def _ensure_namespace_sync(self, namespace: str) -> None:
        labels = {MANAGED_LABEL: "true", **TENANT_NS_PSA_LABELS}
        ns = client.V1Namespace(metadata=client.V1ObjectMeta(name=namespace, labels=labels))
        # 既有 ns 也 patch 补标
        _create_or_patch(
            lambda: self.core.create_namespace(ns),
            lambda: self.core.patch_namespace(namespace, {"metadata": {"labels": labels}}),
        )
        self._ensure_tenant_rbac_sync(namespace)
        self._ensure_default_netpol_sync(namespace)
        self._ensure_quota_sync(namespace)
        self._ensure_limit_range_sync(namespace)
        self._ensure_juicefs_pvc_sync(namespace)

    def _ensure_tenant_rbac_sync(self, namespace: str) -> None:
        """租户 ns 内授予 tenant-mgr 的 secrets Role/RoleBinding(存量 ns 由 patch 收敛)。"""
        role = client.V1Role(
            metadata=client.V1ObjectMeta(
                name=TENANT_MGR_ROLE_NAME, namespace=namespace, labels={MANAGED_LABEL: "true"}
            ),
            rules=[
                client.V1PolicyRule(
                    api_groups=[""],
                    resources=["secrets"],
                    verbs=["get", "create", "patch", "delete"],
                )
            ],
        )
        _create_or_patch(
            lambda: self.rbac.create_namespaced_role(namespace, role),
            lambda: self.rbac.patch_namespaced_role(TENANT_MGR_ROLE_NAME, namespace, role),
        )
        binding = client.V1RoleBinding(
            metadata=client.V1ObjectMeta(
                name=TENANT_MGR_ROLE_NAME, namespace=namespace, labels={MANAGED_LABEL: "true"}
            ),
            role_ref=client.V1RoleRef(
                api_group="rbac.authorization.k8s.io", kind="Role", name=TENANT_MGR_ROLE_NAME
            ),
            subjects=[
                client.RbacV1Subject(
                    kind="ServiceAccount",
                    name=TENANT_MGR_SA_NAME,
                    namespace=self.settings.k8s_platform_namespace,
                )
            ],
        )

        def _patch_binding() -> None:
            # roleRef 不可变,存量绑定只收敛 subjects
            self.rbac.patch_namespaced_role_binding(
                TENANT_MGR_ROLE_NAME, namespace, {"subjects": binding.subjects}
            )

        _create_or_patch(
            lambda: self.rbac.create_namespaced_role_binding(namespace, binding),
            _patch_binding,
        )

    def _ensure_limit_range_sync(self, namespace: str) -> None:
        limits = client.V1LimitRange(
            metadata=client.V1ObjectMeta(
                name="tenant-defaults", namespace=namespace, labels={MANAGED_LABEL: "true"}
            ),
            spec=client.V1LimitRangeSpec(
                limits=[
                    client.V1LimitRangeItem(
                        type="Container",
                        default=TENANT_LIMIT_DEFAULT,
                        default_request=TENANT_LIMIT_DEFAULT_REQUEST,
                    )
                ]
            ),
        )
        _create_or_patch(
            lambda: self.core.create_namespaced_limit_range(namespace, limits),
            lambda: self.core.patch_namespaced_limit_range("tenant-defaults", namespace, limits),
        )

    def _tenant_netpol(
        self, namespace: str, node_gateways: list[str] | None = None
    ) -> "client.V1NetworkPolicy":
        """租户 NetworkPolicy。入方向:默认拒东西向,放行网关数据面(不限端口)与 SSH 22
        (from 排 Pod 网段、逐节点放回 .0/32 与 .1/32,不排私网);出方向:公网除私网/元数据网段,
        TCP 扣黑名单,UDP 白名单 53/443,+ CoreDNS。见 docs/reference/security.md「已接受取舍」。
        """
        return client.V1NetworkPolicy(
            metadata=client.V1ObjectMeta(name="tenant-default", namespace=namespace),
            spec=client.V1NetworkPolicySpec(
                pod_selector=client.V1LabelSelector(),
                policy_types=["Ingress", "Egress"],
                ingress=[
                    # 北向:Envoy 数据面 → 租户 Pod,不限端口
                    client.V1NetworkPolicyIngressRule(
                        _from=[
                            client.V1NetworkPolicyPeer(
                                namespace_selector=client.V1LabelSelector(
                                    match_labels={
                                        "kubernetes.io/metadata.name": GATEWAY_DATAPLANE_NAMESPACE
                                    }
                                )
                            )
                        ],
                    ),
                    # SSH NodePort 入流量:排 Pod 网段,逐节点放回子网网关
                    client.V1NetworkPolicyIngressRule(
                        _from=self._ssh_ingress_peers(node_gateways),
                        ports=[client.V1NetworkPolicyPort(protocol="TCP", port=22)],
                    ),
                ],
                egress=[
                    # DNS:只放 CoreDNS Pod
                    client.V1NetworkPolicyEgressRule(
                        to=[
                            client.V1NetworkPolicyPeer(
                                namespace_selector=client.V1LabelSelector(
                                    match_labels={"kubernetes.io/metadata.name": "kube-system"}
                                ),
                                pod_selector=client.V1LabelSelector(
                                    match_labels={"k8s-app": "kube-dns"}
                                ),
                            )
                        ],
                        ports=[
                            client.V1NetworkPolicyPort(protocol="UDP", port=53),
                            client.V1NetworkPolicyPort(protocol="TCP", port=53),
                        ],
                    ),
                    # 公网 TCP:除私网/元数据网段,端口扣黑名单
                    client.V1NetworkPolicyEgressRule(
                        to=[
                            client.V1NetworkPolicyPeer(
                                ip_block=client.V1IPBlock(cidr="0.0.0.0/0", _except=PRIVATE_CIDRS)
                            )
                        ],
                        ports=_allowed_tcp_port_ranges(),
                    ),
                    # 公网 UDP 白名单(EGRESS_ALLOWED_UDP_PORTS)
                    client.V1NetworkPolicyEgressRule(
                        to=[
                            client.V1NetworkPolicyPeer(
                                ip_block=client.V1IPBlock(cidr="0.0.0.0/0", _except=PRIVATE_CIDRS)
                            )
                        ],
                        ports=[
                            client.V1NetworkPolicyPort(protocol="UDP", port=p)
                            for p in EGRESS_ALLOWED_UDP_PORTS
                        ],
                    ),
                ],
            ),
        )

    def _ssh_ingress_except(self) -> list[str] | None:
        """SSH 入方向的 except 列表(只排 Pod 网段);空配置返回 None,不下发 except。"""
        cidr = (self.settings.tenant_pod_cidr or "").strip()
        return [cidr] if cidr else None

    def _ssh_ingress_peers(self, node_gateways: list[str] | None) -> list[Any]:
        """0.0.0.0/0 排 Pod 网段,再逐个放回节点 Pod 子网网关。"""
        peers: list[Any] = [
            client.V1NetworkPolicyPeer(
                ip_block=client.V1IPBlock(cidr="0.0.0.0/0", _except=self._ssh_ingress_except())
            )
        ]
        if self._ssh_ingress_except():
            peers += [
                client.V1NetworkPolicyPeer(ip_block=client.V1IPBlock(cidr=g))
                for g in (node_gateways or [])
            ]
        return peers

    def _node_pod_gateways_sync(self) -> list[str]:
        """全部节点 spec.podCIDR(s) 的网关 /32;读不到返回空并告警(节点集合变化时重下发)。"""
        try:
            nodes = self._list_all(self.core.list_node)
        except Exception:
            logger.exception("netpol_node_gateways_unavailable")
            return []
        cidrs: list[str] = []
        for n in nodes:
            spec = n.spec
            cidrs += list(spec.pod_cidrs or ([spec.pod_cidr] if spec.pod_cidr else []))
        return pod_cidr_gateways(cidrs)

    def _ensure_default_netpol_sync(self, namespace: str) -> None:
        gateways = self._node_pod_gateways_sync() if self._ssh_ingress_except() else None
        policy = self._tenant_netpol(namespace, gateways)
        _create_or_patch(
            lambda: self.net.create_namespaced_network_policy(namespace, policy),
            lambda: self.net.patch_namespaced_network_policy("tenant-default", namespace, policy),
        )

    def _ensure_quota_sync(self, namespace: str) -> None:
        quota = client.V1ResourceQuota(
            metadata=client.V1ObjectMeta(name="tenant-quota", namespace=namespace),
            spec=client.V1ResourceQuotaSpec(hard=dict(TENANT_QUOTA)),
        )
        _create_or_patch(
            lambda: self.core.create_namespaced_resource_quota(namespace, quota),
            lambda: self.core.patch_namespaced_resource_quota("tenant-quota", namespace, quota),
        )

    def _ensure_juicefs_pvc_sync(self, namespace: str) -> None:
        """每租户 ns 一只共享 JuiceFS PVC(数据盘 subPath 底座);容量为名义值,额度由目录配额管。"""
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
        _ignore(lambda: self.core.create_namespaced_persistent_volume_claim(namespace, pvc), 409)

    # ---------- instance ----------

    async def create_instance(self, spec: InstancePodSpec) -> None:
        await self._run(self._create_instance_sync, spec)

    def _create_instance_sync(self, spec: InstancePodSpec) -> None:
        self._ensure_instance_disk_sync(spec)
        self._ensure_instance_secret_sync(spec)
        self._create_pod_sync(spec)
        self._create_service_sync(spec)
        self._create_httproute_sync(spec)

    # ---------- 平台托管的镜像拉取凭据 ----------

    async def ensure_pull_secret(
        self, namespace: str, dockerconfigjson: str, fingerprint: str
    ) -> None:
        await self._run(self._ensure_pull_secret_sync, namespace, dockerconfigjson, fingerprint)

    def _ensure_pull_secret_sync(
        self, namespace: str, dockerconfigjson: str, fingerprint: str
    ) -> None:
        """superdl-registry-pull:annotation 指纹相同即跳过,不同则 create/patch 覆写。"""
        existing = _ignore(
            lambda: self.core.read_namespaced_secret(PULL_SECRET_NAME, namespace), 404
        )
        if existing is not None:
            annotations = existing.metadata.annotations or {}
            if annotations.get(PULL_SECRET_FINGERPRINT_ANNOTATION) == fingerprint:
                return
        secret = client.V1Secret(
            metadata=client.V1ObjectMeta(
                name=PULL_SECRET_NAME,
                namespace=namespace,
                labels={MANAGED_LABEL: "true"},
                annotations={PULL_SECRET_FINGERPRINT_ANNOTATION: fingerprint},
            ),
            type="kubernetes.io/dockerconfigjson",
            string_data={".dockerconfigjson": dockerconfigjson},
        )
        _create_or_patch(
            lambda: self.core.create_namespaced_secret(namespace, secret),
            lambda: self.core.patch_namespaced_secret(
                PULL_SECRET_NAME,
                namespace,
                {
                    "metadata": {"annotations": {PULL_SECRET_FINGERPRINT_ANNOTATION: fingerprint}},
                    "stringData": {".dockerconfigjson": dockerconfigjson},
                },
            ),
        )

    def _ensure_instance_secret_sync(self, spec: InstancePodSpec) -> None:
        """per-instance 敏感 env 的 Secret(JUPYTER_TOKEN 等);已存在则 patch,随实例删除。"""
        if not spec.secret_env:
            return
        name = instance_env_secret_name(spec.name)
        secret = client.V1Secret(
            metadata=client.V1ObjectMeta(
                name=name,
                namespace=spec.namespace,
                labels={INSTANCE_LABEL: spec.name, MANAGED_LABEL: "true"},
            ),
            string_data=spec.secret_env,
        )
        _create_or_patch(
            lambda: self.core.create_namespaced_secret(spec.namespace, secret),
            lambda: self.core.patch_namespaced_secret(
                name, spec.namespace, {"stringData": spec.secret_env}
            ),
        )

    def _ensure_instance_disk_sync(self, spec: InstancePodSpec) -> None:
        """实例盘 PVC(TopoLVM 节点本地卷,PV 带 node affinity);已存在即跳过,不按新容量重建。"""
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
        _ignore(
            lambda: self.core.create_namespaced_persistent_volume_claim(spec.namespace, pvc), 409
        )

    def _create_pod_sync(self, spec: InstancePodSpec) -> None:
        pod = build_instance_pod(spec)
        try:
            self.core.create_namespaced_pod(spec.namespace, pod)
        except client.ApiException as exc:
            if not _is_conflict(exc):
                raise
            # 同名对象 Terminating 时也是 409,不当幂等成功
            existing: Any = self.core.read_namespaced_pod(spec.name, spec.namespace)
            if existing.metadata.deletion_timestamp is not None:
                raise RuntimeError(
                    f"pod {spec.name} is terminating; create must wait for it to disappear"
                ) from exc

    def _create_service_sync(self, spec: InstancePodSpec) -> None:
        """SSH 走 NodePort(显式端口),Jupyter/服务端点走 ClusterIP,拆成多个 Service。
        dev 建 SSH + Jupyter;service 建(可选 SSH)+ <name>-svc。"""
        if spec.service_port is not None:
            self._create_endpoint_service_sync(spec)
        if not spec.with_ssh:
            return
        if spec.ssh_node_port is None:
            raise RuntimeError(f"instance {spec.name} wants ssh but has no allocated node port")
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
                    )
                ],
            ),
        )
        jupyter_svc = client.V1Service(
            metadata=client.V1ObjectMeta(
                name=jupyter_service_name(spec.name),
                namespace=spec.namespace,
                labels={INSTANCE_LABEL: spec.name, MANAGED_LABEL: "true"},
            ),
            spec=client.V1ServiceSpec(
                type="ClusterIP",
                selector={INSTANCE_LABEL: spec.name},
                ports=[client.V1ServicePort(name="jupyter", port=8888, target_port=8888)],
            ),
        )
        try:
            self.core.create_namespaced_service(spec.namespace, svc)
        except client.ApiException as exc:
            # 409 / 422 都可能是自己的幂等重放,由 _reconcile_ssh_service_conflict_sync 核对
            if not (_is_conflict(exc) or _is_node_port_taken(exc)):
                raise
            self._reconcile_ssh_service_conflict_sync(spec, exc)
        if spec.service_port is None:
            _ignore(lambda: self.core.create_namespaced_service(spec.namespace, jupyter_svc), 409)

    def _create_endpoint_service_sync(self, spec: InstancePodSpec) -> None:
        """服务端点的 ClusterIP Service;端口即用户声明的容器端口。"""
        svc = client.V1Service(
            metadata=client.V1ObjectMeta(
                name=service_endpoint_service_name(spec.name),
                namespace=spec.namespace,
                labels={INSTANCE_LABEL: spec.name, MANAGED_LABEL: "true"},
            ),
            spec=client.V1ServiceSpec(
                type="ClusterIP",
                selector={INSTANCE_LABEL: spec.name},
                ports=[
                    client.V1ServicePort(
                        name="svc", port=spec.service_port, target_port=spec.service_port
                    )
                ],
            ),
        )
        _ignore(lambda: self.core.create_namespaced_service(spec.namespace, svc), 409)

    def _reconcile_ssh_service_conflict_sync(
        self, spec: InstancePodSpec, create_exc: "client.ApiException"
    ) -> None:
        """SSH Service 409/422 核对:同名对象存在且 nodePort 一致 = 幂等成功,漂移则 patch 回;
        同名不存在 = 端口真被占,抛 NodePortTaken。"""
        port = spec.ssh_node_port
        if port is None:
            raise RuntimeError(f"instance {spec.name} ssh service conflict without a node port")
        try:
            existing: Any = self.core.read_namespaced_service(spec.name, spec.namespace)
        except client.ApiException as read_exc:
            if read_exc.status == 404 and _is_node_port_taken(create_exc):
                raise NodePortTaken(port) from create_exc
            raise
        if existing.metadata.deletion_timestamp is not None:
            raise RuntimeError(
                f"service {spec.name} is terminating; create must wait for it to disappear"
            ) from create_exc
        ports = (existing.spec and existing.spec.ports) or []
        current = ports[0].node_port if ports else None
        if current == spec.ssh_node_port:
            return  # 幂等成功:已创建的就是期望端口
        try:
            self.core.patch_namespaced_service(
                spec.name,
                spec.namespace,
                {
                    "spec": {
                        "ports": [
                            {
                                "name": "ssh",
                                "port": 22,
                                "targetPort": 22,
                                "nodePort": spec.ssh_node_port,
                            }
                        ]
                    }
                },
            )
        except client.ApiException as patch_exc:
            # 期望端口被其它对象占用,交编排层换端口
            if _is_node_port_taken(patch_exc):
                raise NodePortTaken(port) from patch_exc
            raise

    def _httproute_body(self, spec: InstancePodSpec) -> dict[str, Any]:
        """租户实例的 HTTPRoute:dev 指向 Jupyter,service 指向用户容器端口。
        路由在租户 ns、Gateway 在平台 ns,由 listener allowedRoutes Selector 授权;sectionName 必填
        且服务路由必须挂 GATEWAY_SVC_LISTENER(只有它挂 extAuth)。"""
        if spec.service_port is not None:
            if not spec.service_host:
                raise RuntimeError(f"instance {spec.name} has service_port but no service_host")
            listener, hostname, backend, port = (
                GATEWAY_SVC_LISTENER,
                spec.service_host,
                service_endpoint_service_name(spec.name),
                spec.service_port,
            )
        else:
            listener, hostname, backend, port = (
                GATEWAY_APP_LISTENER,
                spec.jupyter_host,
                jupyter_service_name(spec.name),
                8888,
            )
        return {
            "apiVersion": f"{GATEWAY_API_GROUP}/{GATEWAY_API_VERSION}",
            "kind": "HTTPRoute",
            "metadata": {
                "name": spec.name,
                "namespace": spec.namespace,
                "labels": {INSTANCE_LABEL: spec.name, MANAGED_LABEL: "true"},
            },
            "spec": {
                "parentRefs": [
                    {
                        "group": GATEWAY_API_GROUP,
                        "kind": "Gateway",
                        "name": GATEWAY_NAME,
                        "namespace": GATEWAY_NAMESPACE,
                        "sectionName": listener,
                    }
                ],
                "hostnames": [hostname],
                "rules": [
                    {
                        "matches": [{"path": {"type": "PathPrefix", "value": "/"}}],
                        "backendRefs": [{"name": backend, "port": port}],
                    }
                ],
            },
        }

    def _create_httproute_sync(self, spec: InstancePodSpec) -> None:
        _ignore(
            lambda: self.custom.create_namespaced_custom_object(
                GATEWAY_API_GROUP,
                GATEWAY_API_VERSION,
                spec.namespace,
                HTTPROUTE_PLURAL,
                self._httproute_body(spec),
            ),
            409,
        )

    async def delete_instance(self, namespace: str, name: str, *, force: bool = False) -> None:
        await self._run(self._delete_instance_sync, namespace, name, force)

    def _delete_instance_sync(self, namespace: str, name: str, force: bool = False) -> None:
        # 强删(grace 0)直接摘对象
        pod_kwargs = {"grace_period_seconds": 0} if force else {}
        for deleter in (
            lambda: self.core.delete_namespaced_pod(name, namespace, **pod_kwargs),
            lambda: self.core.delete_namespaced_service(name, namespace),
            lambda: self.core.delete_namespaced_service(jupyter_service_name(name), namespace),
            lambda: self.core.delete_namespaced_service(
                service_endpoint_service_name(name), namespace
            ),
            lambda: self.core.delete_namespaced_secret(instance_env_secret_name(name), namespace),
            lambda: self.custom.delete_namespaced_custom_object(
                GATEWAY_API_GROUP, GATEWAY_API_VERSION, namespace, HTTPROUTE_PLURAL, name
            ),
        ):
            _ignore(deleter, 404)

    async def delete_instance_disk(self, namespace: str, name: str) -> None:
        await self._run(self._delete_instance_disk_sync, namespace, name)

    def _delete_instance_disk_sync(self, namespace: str, name: str) -> None:
        _ignore(
            lambda: self.core.delete_namespaced_persistent_volume_claim(
                instance_disk_pvc_name(name), namespace
            ),
            404,
        )

    async def get_status(self, namespace: str, name: str) -> PodStatus:
        return await self._run(self._get_status_sync, namespace, name)

    def _get_status_sync(self, namespace: str, name: str) -> PodStatus:
        pod: Any = _ignore(lambda: self.core.read_namespaced_pod(name, namespace), 404)
        if pod is None:
            return PodStatus(exists=False)
        return self._pod_status(pod)

    @staticmethod
    def _pod_status(pod: Any) -> PodStatus:
        return PodStatus(
            exists=True,
            ready=_ready_condition(pod),
            phase=pod.status.phase or "Unknown",
            node_name=pod.spec.node_name,
            deleting=pod.metadata.deletion_timestamp is not None,
            namespace=pod.metadata.namespace,
            name=pod.metadata.name,
            labels=dict(pod.metadata.labels or {}),
        )

    async def read_instance_logs(self, namespace: str, name: str, *, tail_lines: int) -> str:
        return await self._run(self._read_instance_logs_sync, namespace, name, tail_lines)

    def _read_instance_logs_sync(self, namespace: str, name: str, tail_lines: int) -> str:
        # 日志读超时收紧到 5s
        return cast(
            str,
            self.core.read_namespaced_pod_log(
                name,
                namespace,
                container="workspace",
                tail_lines=tail_lines,
                timestamps=True,
                _request_timeout=(5.0, 5.0),
            ),
        )

    async def list_instance_pods(self) -> list[PodStatus]:
        return await self._run(self._list_instance_pods_sync)

    def _list_instance_pods_sync(self) -> list[PodStatus]:
        pods = self._list_all(self.core.list_pod_for_all_namespaces, label_selector=MANAGED_LABEL)
        prefix = self.settings.k8s_namespace_prefix
        return [self._pod_status(p) for p in pods if p.metadata.namespace.startswith(prefix)]

    async def list_instance_endpoints(self) -> list[tuple[str, str]]:
        return await self._run(self._list_instance_endpoints_sync)

    def _list_instance_endpoints_sync(self) -> list[tuple[str, str]]:
        prefix = self.settings.k8s_namespace_prefix
        out: set[tuple[str, str]] = set()
        for svc in self._list_all(
            self.core.list_service_for_all_namespaces, label_selector=MANAGED_LABEL
        ):
            ns = svc.metadata.namespace
            if not ns.startswith(prefix):
                continue
            name = svc.metadata.name
            # 副名归并到实例名(<uuid>-jupyter / <uuid>-svc → <uuid>)
            for suffix in ("-jupyter", "-svc"):
                if name.endswith(suffix):
                    name = name[: -len(suffix)]
                    break
            out.add((ns, name))
        for route in self._list_all_custom(
            self.custom.list_cluster_custom_object,
            GATEWAY_API_GROUP,
            GATEWAY_API_VERSION,
            HTTPROUTE_PLURAL,
            label_selector=MANAGED_LABEL,
        ):
            meta = route.get("metadata") or {}
            ns = meta.get("namespace") or ""
            if ns.startswith(prefix):
                out.add((ns, meta.get("name") or ""))
        return sorted(out)

    async def used_node_ports(self) -> set[int]:
        return await self._run(self._used_node_ports_sync)

    def _used_node_ports_sync(self) -> set[int]:
        # 列全集群 Service 的 NodePort,不按 MANAGED_LABEL 过滤(blocked 端口的占用者是非平台对象)
        ports: set[int] = set()
        for svc in self._list_all(self.core.list_service_for_all_namespaces):
            for p in svc.spec.ports or []:
                if p.node_port:
                    ports.add(p.node_port)
        return ports

    # ---------- 数据盘擦除 ----------

    def _run_managed_job_sync(
        self,
        namespace: str,
        job_name: str,
        container: Any,
        volumes: list[Any],
        pod_labels: dict[str, str],
    ) -> None:
        """受管 Job 生命周期(wipe/quota 共用):已成功 → 清理返回;进行中 → 抛错待重试;
        失败 → 删 Job 重建;不存在 → 创建并抛错等下轮。"""
        existing: Any = _ignore(lambda: self.batch.read_namespaced_job(job_name, namespace), 404)
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
                raise RuntimeError(f"job failed, recreated next retry: {job_name}")
            raise RuntimeError(f"job still running: {job_name}")
        job = build_managed_job(namespace, job_name, container, volumes, pod_labels)
        _ignore(lambda: self.batch.create_namespaced_job(namespace, job), 409)
        raise RuntimeError(f"job created, awaiting completion: {job_name}")

    @staticmethod
    def _batch_container(
        name: str, image: str, command: list[str], env: list[Any], *, non_root: bool = False
    ) -> Any:
        """一次性 Job 容器基座(wipe/quota/prewarm 共用):资源声明 + 安全上下文;
        non_root=True 走 platform_job_security_context,wipe 在租户 ns 保持 root。"""
        return client.V1Container(
            name=name,
            image=image,
            command=command,
            env=env,
            # 租户 ns ResourceQuota 要求声明 request/limit
            resources=client.V1ResourceRequirements(
                requests={"cpu": "10m", "memory": "16Mi", "ephemeral-storage": "16Mi"},
                limits={"cpu": "100m", "memory": "64Mi", "ephemeral-storage": "64Mi"},
            ),
            security_context=(
                platform_job_security_context() if non_root else tenant_security_context()
            ),
        )

    async def wipe_disk(self, namespace: str, subpath: str) -> None:
        await self._run(self._wipe_disk_sync, namespace, subpath)

    def _wipe_disk_sync(self, namespace: str, subpath: str) -> None:
        """租户 ns 内起 Job 挂 JuiceFS PVC 删除子目录(幂等,见 _run_managed_job_sync)。"""
        _check_subpath(subpath)
        container = self._batch_container(
            "wipe", WIPE_IMAGE, ["rm", "-rf", f"/data/{subpath}"], env=[]
        )
        container.volume_mounts = [client.V1VolumeMount(name="juicefs", mount_path="/data")]
        self._run_managed_job_sync(
            namespace,
            f"wipe-{subpath[-40:]}".lower(),
            container,
            volumes=[
                client.V1Volume(
                    name="juicefs",
                    persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                        claim_name=JUICEFS_PVC_NAME
                    ),
                )
            ],
            pod_labels={},
        )

    # ---------- JuiceFS 目录配额(平台 ns,纯元数据操作,不挂卷) ----------

    def _juicefs_fs_base_sync(self, namespace: str) -> str:
        """租户共享 PVC 在 JuiceFS 文件系统根下的子目录名(PV volumeAttributes.subPath,默认 PV 名);
        配额 --path 必须带这层前缀。"""
        pvc: Any = self.core.read_namespaced_persistent_volume_claim(JUICEFS_PVC_NAME, namespace)
        pv_name = pvc.spec.volume_name if pvc.spec else None
        if not pv_name:
            raise RuntimeError(f"pvc {namespace}/{JUICEFS_PVC_NAME} not bound yet")
        pv: Any = self.core.read_persistent_volume(pv_name)
        attrs = (pv.spec.csi.volume_attributes if pv.spec and pv.spec.csi else None) or {}
        base = attrs.get("subPath") or pv_name
        _check_subpath(base)
        return base

    async def set_disk_quota(self, namespace: str, subpath: str, capacity_gb: int) -> None:
        await self._run(self._disk_quota_sync, namespace, subpath, capacity_gb, True)

    async def delete_disk_quota(self, namespace: str, subpath: str) -> None:
        await self._run(self._disk_quota_sync, namespace, subpath, 0, False)

    def _disk_quota_sync(
        self, namespace: str, subpath: str, capacity_gb: int, is_set: bool
    ) -> None:
        """平台 ns 起 juicefs CLI Job 下发/摘除目录配额(幂等,见 _run_managed_job_sync)。
        metaurl 经 secretKeyRef 注入;subpath/capacity 走 env;密码拆到 META_PASSWORD,不进 argv。"""
        _check_subpath(subpath)
        fs_base = self._juicefs_fs_base_sync(namespace)
        container = build_disk_quota_container(
            self.settings.juicefs_cli_image, fs_base, subpath, capacity_gb, is_set
        )
        action = "set" if is_set else "del"
        self._run_managed_job_sync(
            self.settings.k8s_platform_namespace,
            f"quota-{action}-{subpath[-36:]}".lower(),
            container,
            volumes=[],
            # NetworkPolicy jobs-egress 按此标签放行 PG 出向
            pod_labels={"app": "superdl-disk-quota"},
        )

    # ---------- 节点 ----------

    @staticmethod
    def _gpu_amount(resources: dict[str, Any] | None) -> int:
        """整卡 + MIG 分片统一计数(HAMi 池的 nvidia.com/gpu 为虚拟化后份额)。"""
        total = 0
        for key, value in (resources or {}).items():
            if key == "nvidia.com/gpu" or key.startswith("nvidia.com/mig-"):
                total += int(value)
        return total

    @staticmethod
    def _physical_gpu_amount(node: Any) -> int:
        """节点物理卡数:以 GFD 标签 nvidia.com/gpu.count 为准;非切分池无标签按 allocatable;
        hami 池缺标签计 0 并告警。"""
        labels = node.metadata.labels or {}
        gfd = labels.get("nvidia.com/gpu.count")
        allocatable = RealOrchestrator._gpu_amount(node.status.allocatable)
        if gfd and str(gfd).isdigit():
            physical = int(gfd)
            if 0 < physical < allocatable:
                return physical
        if labels.get(POOL_NODE_LABEL) == "hami" and allocatable > 0:
            logger.error(
                "hami_node_missing_gfd_label",
                node=node.metadata.name,
                allocatable=allocatable,
                hint="切分池节点缺 nvidia.com/gpu.count:按 0 纳管防超卖,查 GFD 与节点标签",
            )
            return 0
        return allocatable

    @staticmethod
    def _pod_gpu_occupancy(limits: dict[str, Any]) -> float:
        """Pod 占用的物理卡当量:HAMi 按 gpucores 折算(N 虚卡 × X% = N·X/100);整卡/MIG 按 1/个。"""
        whole = RealOrchestrator._gpu_amount(limits)
        cores = limits.get("nvidia.com/gpucores")
        if cores is not None and str(cores).isdigit() and whole > 0:
            return whole * int(cores) / 100.0
        return float(whole)

    @staticmethod
    def _list_all(list_fn: Any, **kwargs: Any) -> list[Any]:
        """分页拉满全量。"""
        items: list[Any] = []
        kwargs["limit"] = 500
        cont: str | None = None
        while True:
            if cont:
                kwargs["_continue"] = cont
            page: Any = list_fn(**kwargs)
            items.extend(page.items)
            cont = getattr(page.metadata, "_continue", None)
            if not cont:
                return items

    @staticmethod
    def _list_all_custom(list_fn: Any, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """_list_all 的 CustomObjectsApi 版(裸 dict,游标在 `metadata.continue`)。"""
        items: list[dict[str, Any]] = []
        kwargs["limit"] = 500
        cont: str | None = None
        while True:
            if cont:
                kwargs["_continue"] = cont
            page: Any = list_fn(*args, **kwargs)
            items.extend(page.get("items") or [])
            cont = (page.get("metadata") or {}).get("continue")
            if not cont:
                return items

    def _used_gpus_by_node(self) -> dict[str, int]:
        """全部受管 Pod 按节点聚合已用份额(物理卡当量,HAMi 折算后向上取整);未调度 Pod 跳过。"""
        pods = self._list_all(
            self.core.list_pod_for_all_namespaces,
            label_selector=MANAGED_LABEL,
            field_selector="status.phase!=Failed",
        )
        used: dict[str, float] = {}
        for pod in pods:
            node = pod.spec.node_name
            if not node:
                continue
            for c in pod.spec.containers:
                limits = (c.resources and c.resources.limits) or {}
                used[node] = used.get(node, 0.0) + self._pod_gpu_occupancy(limits)
        return {node: math.ceil(v) for node, v in used.items()}

    async def list_nodes(self, include_unlabeled: bool = False) -> list[NodeInfo]:
        return await self._run(self._list_nodes_sync, include_unlabeled)

    async def set_node_labels(self, node_name: str, labels: dict[str, str]) -> None:
        await self._run(self.core.patch_node, node_name, {"metadata": {"labels": labels}})

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
        # 长后缀优先("Ki" 先于 "K")
        for suf in sorted(units, key=len, reverse=True):
            if q.endswith(suf):
                try:
                    return int(float(q[: -len(suf)]) * units[suf])
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

    @staticmethod
    def _gfd_version(labels: dict[str, str], kind: str) -> str:
        """GFD 版本标签:优先 <kind>-version.full,回落 major/minor(/revision)拼接。"""
        full = labels.get(f"nvidia.com/cuda.{kind}-version.full")
        if full:
            return full
        parts = [
            labels.get(f"nvidia.com/cuda.{kind}-version.{k}")
            for k in ("major", "minor", "revision")
        ]
        return ".".join(p for p in parts if p)

    def _list_nodes_sync(self, include_unlabeled: bool = False) -> list[NodeInfo]:
        selector = None if include_unlabeled else POOL_NODE_LABEL
        nodes = self._list_all(self.core.list_node, label_selector=selector)
        used_by_node = self._used_gpus_by_node()  # 一次拉取全量,避免逐节点扫 Pod
        out: list[NodeInfo] = []
        for node in nodes:
            labels = node.metadata.labels or {}
            ready = _ready_condition(node)
            cordoned = bool(node.spec.unschedulable)
            total = self._physical_gpu_amount(node)
            cap = node.status.capacity or {}
            out.append(
                NodeInfo(
                    name=node.metadata.name,
                    pool_label=labels.get(POOL_NODE_LABEL, "unknown"),
                    gpu_total=total,
                    gpu_used=min(total, used_by_node.get(node.metadata.name, 0)),
                    status="Cordoned" if cordoned else ("Ready" if ready else "NotReady"),
                    vcpu=self._cpu_cores(cap.get("cpu")),
                    mem_gb=self._qty_to_bytes(cap.get("memory")) // 1024**3,
                    disk_gb=self._qty_to_bytes(cap.get("ephemeral-storage")) // 1024**3,
                    gpu_model_label=labels.get("nvidia.com/gpu.product", ""),
                    model_label_current=labels.get(GPU_MODEL_NODE_LABEL, ""),
                    driver_version_label=self._gfd_version(labels, "driver")[:32],
                    cuda_version_label=self._gfd_version(labels, "runtime")[:16],
                )
            )
        return out

    async def probe_cluster(self) -> ClusterProbe:
        return await self._run(self._probe_cluster_sync)

    def _probe_cluster_sync(self) -> ClusterProbe:
        # 版本失败 = API 不可达,整体判不可用;组件清点逐项容错
        try:
            version: Any = self._version.get_code()
            git_version = getattr(version, "git_version", None)
        except Exception as exc:
            return ClusterProbe(api_reachable=False, error=str(exc))
        errors: list[str] = []
        apps = self._apps
        hami_ready = dcgm = kps = gpu_operator = False
        gateway_ready = cert_manager_ready = False
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
                # 租户入口 TLS 证书
                if name == "cert-manager":
                    cert_manager_ready = bool(d.status.ready_replicas)
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
        # 网关就绪看 Gateway 对象的 Programmed 条件;404(CRD 未装 / 未下发)算没就绪,不记 error
        try:
            gw: Any = self.custom.get_namespaced_custom_object(
                GATEWAY_API_GROUP,
                GATEWAY_API_VERSION,
                GATEWAY_NAMESPACE,
                GATEWAY_PLURAL,
                GATEWAY_NAME,
            )
            gateway_ready = any(
                c.get("type") == "Programmed" and c.get("status") == "True"
                for c in ((gw.get("status") or {}).get("conditions") or [])
            )
        except client.ApiException as exc:
            if exc.status != 404:
                errors.append(f"gateway: {exc.status}")
        runtime_classes: tuple[str, ...] = ()
        try:
            rcs: Any = self._node.list_runtime_class()
            runtime_classes = tuple(rc.metadata.name for rc in rcs.items)
        except client.ApiException as exc:
            errors.append(f"runtimeclasses: {exc.status}")
        storage_classes: tuple[str, ...] = ()
        try:
            scs: Any = self._storage.list_storage_class()
            storage_classes = tuple(sc.metadata.name for sc in scs.items)
        except client.ApiException as exc:
            errors.append(f"storageclasses: {exc.status}")
        pools: dict[str, int] = {}
        nodes_ready = nodes_total = 0
        try:
            # 只数池标签与就绪面,不走 _list_nodes_sync
            for node in self._list_all(self.core.list_node):
                key = (node.metadata.labels or {}).get(POOL_NODE_LABEL, "unlabeled")
                pools[key] = pools.get(key, 0) + 1
                nodes_total += 1
                if _ready_condition(node) and not node.spec.unschedulable:
                    nodes_ready += 1
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
            nvidia_runtimeclass="nvidia" in runtime_classes,
            gateway_ready=gateway_ready,
            cert_manager_ready=cert_manager_ready,
            nodes_ready=nodes_ready,
            nodes_total=nodes_total,
            storage_classes=storage_classes,
            pools=pools,
            error="; ".join(errors) or None,
        )

    async def set_node_unschedulable(self, node_name: str, unschedulable: bool) -> None:
        await self._run(self._set_node_unschedulable_sync, node_name, unschedulable)

    def _set_node_unschedulable_sync(self, node_name: str, unschedulable: bool) -> None:
        # 需 ClusterRole nodes patch(deploy/app/k8s/01-rbac.yaml)
        self.core.patch_node(node_name, {"spec": {"unschedulable": unschedulable}})

    async def delete_node(self, node_name: str) -> None:
        await self._run(self._delete_node_sync, node_name)

    def _delete_node_sync(self, node_name: str) -> None:
        """cordon → 删 Node,两步都吞 404;需 ClusterRole nodes patch + delete(01-rbac.yaml)。"""
        _ignore(
            lambda: self.core.patch_node(node_name, {"spec": {"unschedulable": True}}),
            404,
        )
        _ignore(lambda: self.core.delete_node(node_name), 404)

    # ---------- 镜像预热 ----------

    @staticmethod
    def _prewarm_job_name(node_name: str, image_ref: str) -> str:
        """确定性 Job 名(≤63 字符),同(节点,镜像)幂等;SHA1 仅作压缩指纹。"""
        ref_hash = hashlib.sha1(image_ref.encode(), usedforsecurity=False).hexdigest()[:10]
        node_hash = hashlib.sha1(node_name.encode(), usedforsecurity=False).hexdigest()[:8]
        return f"prewarm-{ref_hash}-{node_hash}"

    async def prewarm_image(
        self, node_name: str, image_ref: str, *, image_pull_secret: str | None = None
    ) -> None:
        await self._run(self._prewarm_image_sync, node_name, image_ref, image_pull_secret)

    def _prewarm_image_sync(
        self, node_name: str, image_ref: str, image_pull_secret: str | None
    ) -> None:
        """nodeName 定点起拉取 Job,创建即返回(完成态由 prewarm_patrol 经 get_prewarm_status 收敛);
        同名已存在则跳过。"""
        job_name = self._prewarm_job_name(node_name, image_ref)
        if _ignore(
            lambda: self.batch.read_namespaced_job(job_name, self.settings.k8s_platform_namespace),
            404,
        ):
            return  # 幂等:任意状态的既有 Job 都交巡检收敛
        job = build_prewarm_job(
            self.settings.k8s_platform_namespace, job_name, node_name, image_ref, image_pull_secret
        )
        _ignore(
            lambda: self.batch.create_namespaced_job(self.settings.k8s_platform_namespace, job), 409
        )

    async def get_prewarm_status(self, node_name: str, image_ref: str) -> PrewarmJobStatus:
        return await self._run(self._get_prewarm_status_sync, node_name, image_ref)

    def _get_prewarm_status_sync(self, node_name: str, image_ref: str) -> PrewarmJobStatus:
        job_name = self._prewarm_job_name(node_name, image_ref)
        job: Any = _ignore(
            lambda: self.batch.read_namespaced_job(job_name, self.settings.k8s_platform_namespace),
            404,
        )
        if job is None:
            return PrewarmJobStatus(state="absent")
        if (job.status.succeeded or 0) >= 1:
            return PrewarmJobStatus(state="succeeded")
        if (job.status.failed or 0) >= 1:
            return PrewarmJobStatus(state="failed", message=self._prewarm_failure_sync(job_name))
        return PrewarmJobStatus(state="running")

    def _prewarm_failure_sync(self, job_name: str) -> str:
        """失败原因:优先 Pod 容器态(ErrImagePull 等),兜底 Job condition。"""
        try:
            pods: Any = self.core.list_namespaced_pod(
                self.settings.k8s_platform_namespace, label_selector=f"job-name={job_name}"
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
        await self._run(self._delete_prewarm_job_sync, node_name, image_ref)

    def _delete_prewarm_job_sync(self, node_name: str, image_ref: str) -> None:
        job_name = self._prewarm_job_name(node_name, image_ref)
        _ignore(
            lambda: self.batch.delete_namespaced_job(
                job_name, self.settings.k8s_platform_namespace, propagation_policy="Background"
            ),
            404,
        )


def build_disk_quota_container(
    image: str, fs_base: str, subpath: str, capacity_gb: int, is_set: bool
) -> Any:
    """配额 Job 容器(纯构造):metaurl 经 secretKeyRef 注入;subpath/capacity 走 env;
    密码拆到 META_PASSWORD,不进 argv。"""
    # 密码拆分在容器内 shell 完成;metaurl 密码段约定不含 @
    split = (
        'export META_PASSWORD="$(printf \'%s\' "$JUICEFS_METAURL"'
        " | sed -n 's|^[^:]*://[^:]*:\\([^@]*\\)@.*|\\1|p')\"; "
        'METAURL_NOPASS="$(printf \'%s\' "$JUICEFS_METAURL"'
        " | sed 's|^\\([^:]*://[^:]*\\):[^@]*@|\\1@|')\"; "
    )
    if is_set:
        script = (
            split + 'juicefs quota set "$METAURL_NOPASS" --path "/$QUOTA_BASE/$QUOTA_SUBPATH"'
            ' --capacity "$QUOTA_CAPACITY_GB" --create'
        )
    else:
        # 删盘链路:无配额记录不算失败
        script = (
            split + 'juicefs quota delete "$METAURL_NOPASS" --path "/$QUOTA_BASE/$QUOTA_SUBPATH"'
            " || true"
        )
    container = RealOrchestrator._batch_container(
        "quota", image, ["sh", "-c", script], env=[], non_root=True
    )
    container.env = [
        client.V1EnvVar(
            name="JUICEFS_METAURL",
            value_from=client.V1EnvVarSource(
                secret_key_ref=client.V1SecretKeySelector(name="superdl-db", key="juicefs-metaurl")
            ),
        ),
        client.V1EnvVar(name="QUOTA_BASE", value=fs_base),
        client.V1EnvVar(name="QUOTA_SUBPATH", value=subpath),
        client.V1EnvVar(name="QUOTA_CAPACITY_GB", value=str(capacity_gb)),
    ]
    return container


def build_prewarm_job(
    platform_namespace: str,
    job_name: str,
    node_name: str,
    image_ref: str,
    image_pull_secret: str | None,
) -> "client.V1Job":
    """预热 Job 对象(纯构造):nodeName 定点、纯拉取触发(命令为 true)、restricted 非 root 上下文;
    镜像缺 sh 由巡检记 failed。"""
    container = RealOrchestrator._batch_container(
        "prewarm", image_ref, ["/bin/sh", "-c", "true"], env=[], non_root=True
    )
    # IfNotPresent;换版本靠目录 image_ref 钉 digest,见 deploy/instance-images/README.md
    container.image_pull_policy = "IfNotPresent"
    return client.V1Job(
        metadata=client.V1ObjectMeta(
            name=job_name,
            namespace=platform_namespace,
            labels={PREWARM_LABEL: "true"},
            annotations={"superdl.io/node": node_name, "superdl.io/image": image_ref},
        ),
        spec=client.V1JobSpec(
            backoff_limit=0,  # 失败不原地重试,由巡检删 Job 后重建(带退避节流)
            ttl_seconds_after_finished=600,
            active_deadline_seconds=1800,  # 20GB 级镜像上限
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(labels={PREWARM_LABEL: "true"}),
                spec=client.V1PodSpec(
                    node_name=node_name,  # 绕过调度器定点拉取
                    restart_policy="Never",
                    automount_service_account_token=False,
                    image_pull_secrets=(
                        [client.V1LocalObjectReference(name=image_pull_secret)]
                        if image_pull_secret
                        else None
                    ),
                    # 容忍一切污点(预热覆盖 cordon 节点)
                    tolerations=[client.V1Toleration(operator="Exists")],
                    containers=[container],
                ),
            ),
        ),
    )
