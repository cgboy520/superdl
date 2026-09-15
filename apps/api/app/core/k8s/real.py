"""K8s 编排实现;异步入口通过专属执行器调用官方同步客户端。

实例 Pod、SSH Service 和 HTTPRoute 使用实例名;Jupyter 和服务端点 Service 分别加后缀。
"""

import asyncio
import hashlib
import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, cast

from kubernetes import client, config

from app.core.config import get_settings
from app.core.k8s import health
from app.core.k8s.base import (
    DATA_DISK_STORAGE_CLASS,
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
    instance_disk_pvc_name,
    instance_env_secret_name,
    jupyter_service_name,
    service_endpoint_service_name,
)
from app.core.k8s.health import (
    ListenerRow,
    NodeRow,
    RuntimeClassRow,
    StorageClassRow,
    WorkloadRow,
)
from app.core.logging import get_logger
from app.core.registry import PULL_SECRET_FINGERPRINT_ANNOTATION, PULL_SECRET_NAME

INSTANCE_LABEL = "superdl.io/instance"
PREWARM_LABEL = "superdl.io/prewarm"
GATEWAY_DATAPLANE_NAMESPACE = "envoy-gateway-system"


TENANT_NS_PSA_LABELS = {
    "pod-security.kubernetes.io/enforce": "baseline",
    "pod-security.kubernetes.io/audit": "restricted",
    "pod-security.kubernetes.io/warn": "restricted",
}


def tenant_security_context() -> "client.V1SecurityContext":
    """租户容器基线:禁提权、RuntimeDefault seccomp,仅保留 SYS_CHROOT/SETUID/SETGID。"""
    return client.V1SecurityContext(
        allow_privilege_escalation=False,
        capabilities=client.V1Capabilities(drop=["ALL"], add=["SYS_CHROOT", "SETUID", "SETGID"]),
        seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"),
    )


def platform_job_security_context() -> "client.V1SecurityContext":
    """平台 Job 的非 root 上下文:UID/GID 65534、禁提权、零 capability、RuntimeDefault seccomp。"""
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
TENANT_LIMIT_DEFAULT_REQUEST = {"cpu": "100m", "memory": "256Mi", "ephemeral-storage": "1Gi"}
TENANT_LIMIT_DEFAULT = {"cpu": "8", "memory": "32Gi", "ephemeral-storage": "64Gi"}
TENANT_MGR_ROLE_NAME = "superdl-tenant-mgr-secrets"
TENANT_MGR_SA_NAME = "superdl-tenant-mgr"
PRIVATE_CIDRS = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "100.64.0.0/10",
    "198.18.0.0/15",
]
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
EGRESS_ALLOWED_UDP_PORTS = (53, 443)

WIPE_IMAGE = "busybox:1.36@sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662"
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
    """构造实例 Pod,不调用 API;secret_env 通过 Secret 引用注入。"""
    requests = {
        "cpu": str(spec.vcpu),
        "memory": f"{spec.mem_gb}Gi",
        "ephemeral-storage": TENANT_EPHEMERAL_REQUEST,
        **spec.gpu_resources,
    }
    limits = {**requests, "ephemeral-storage": TENANT_EPHEMERAL_LIMIT}
    env = [client.V1EnvVar(name=k, value=v) for k, v in spec.env.items()]
    secret_name = instance_env_secret_name(spec.name)
    env.extend(
        client.V1EnvVar(
            name=key,
            value_from=client.V1EnvVarSource(
                secret_key_ref=client.V1SecretKeySelector(name=secret_name, key=key)
            ),
        )
        for key in spec.secret_env
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
    if spec.data_disk_pvc:
        volumes.append(
            client.V1Volume(
                name="data-disk",
                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                    claim_name=spec.data_disk_pvc
                ),
            )
        )
        mounts.append(client.V1VolumeMount(name="data-disk", mount_path="/root/data"))
    return client.V1Pod(
        metadata=client.V1ObjectMeta(
            name=spec.name,
            namespace=spec.namespace,
            labels={INSTANCE_LABEL: spec.name, MANAGED_LABEL: "true"},
            annotations=spec.annotations or None,
        ),
        spec=client.V1PodSpec(
            runtime_class_name=spec.runtime_class,
            scheduler_name=spec.scheduler_name,
            host_users=False if spec.host_users is False else None,
            restart_policy=spec.restart_policy,
            node_selector=spec.node_selector or None,
            termination_grace_period_seconds=30,
            automount_service_account_token=False,
            enable_service_links=False,
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


_HAMI_SCHEDULER = "hami-scheduler"
_HAMI_DEVICE_PLUGIN = "hami-device-plugin"
_CERT_MANAGER_DEPLOYS = ("cert-manager", "cert-manager-webhook", "cert-manager-cainjector")
_GPU_ALLOCATABLE = "nvidia.com/gpu"
_DRIVER_LABEL = "nvidia.com/cuda.driver-version.full"


@dataclass
class _Workloads:
    """按体检项归类的平台工作负载就绪事实。"""

    hami_scheduler: WorkloadRow = field(default_factory=lambda: WorkloadRow(_HAMI_SCHEDULER))
    hami_device_plugin: WorkloadRow = field(
        default_factory=lambda: WorkloadRow(_HAMI_DEVICE_PLUGIN)
    )
    gpu_operator_ns: str = ""
    gpu_operands: list[WorkloadRow] = field(default_factory=list)
    dcgm: list[WorkloadRow] = field(default_factory=list)
    cert_manager: list[WorkloadRow] = field(default_factory=list)
    kata_deploy: WorkloadRow = field(default_factory=lambda: WorkloadRow("kata-deploy"))
    prometheus: WorkloadRow = field(default_factory=lambda: WorkloadRow("prometheus"))
    alertmanager: WorkloadRow = field(default_factory=lambda: WorkloadRow("alertmanager"))
    gpu_operator_present: bool = False
    kps_present: bool = False


def _safe_int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _first_image(template: Any) -> str:
    containers = getattr(getattr(template, "spec", None), "containers", None) or []
    return str(getattr(containers[0], "image", "") or "") if containers else ""


def _condition_reason(conditions: Any) -> str:
    """返回非 True 条件中的首个非空 reason,没有则返回空串。"""
    for c in conditions or []:
        if getattr(c, "status", "") != "True":
            reason = str(getattr(c, "reason", "") or "")
            if reason:
                return reason
    return ""


def _deploy_row(d: Any) -> WorkloadRow:
    return WorkloadRow(
        name=str(d.metadata.name or ""),
        namespace=str(d.metadata.namespace or ""),
        ready=_safe_int(d.status.ready_replicas),
        desired=_safe_int(d.spec.replicas),
        image=_first_image(d.spec.template),
        reason=_condition_reason(d.status.conditions),
    )


def _ds_row(ds: Any) -> WorkloadRow:
    return WorkloadRow(
        name=str(ds.metadata.name or ""),
        namespace=str(ds.metadata.namespace or ""),
        ready=_safe_int(ds.status.number_ready),
        desired=_safe_int(ds.status.desired_number_scheduled),
        image=_first_image(ds.spec.template),
    )


def _sts_row(st: Any) -> WorkloadRow:
    return WorkloadRow(
        name=str(st.metadata.name or ""),
        namespace=str(st.metadata.namespace or ""),
        ready=_safe_int(st.status.ready_replicas),
        desired=_safe_int(st.spec.replicas),
        image=_first_image(st.spec.template),
    )


def _listener_rows(gw: dict[str, Any]) -> list[ListenerRow]:
    """status.listeners 与 spec.listeners 按名对齐:端口协议在 spec,挂载数与条件在 status。"""
    status: dict[str, Any] = gw.get("status") or {}
    spec_by_name = {
        str(lis.get("name", "")): lis for lis in ((gw.get("spec") or {}).get("listeners") or [])
    }
    rows: list[ListenerRow] = []
    for lis in status.get("listeners") or []:
        name = str(lis.get("name", ""))
        spec_lis: dict[str, Any] = spec_by_name.get(name) or {}
        conds = lis.get("conditions") or []
        rows.append(
            ListenerRow(
                name=name,
                port=_safe_int(spec_lis.get("port")),
                protocol=str(spec_lis.get("protocol") or ""),
                attached=_safe_int(lis.get("attachedRoutes")),
                programmed=any(
                    c.get("type") == "Programmed" and c.get("status") == "True" for c in conds
                ),
                reason=next(
                    (str(c.get("reason") or "") for c in conds if c.get("status") != "True"), ""
                ),
            )
        )
    return rows


@dataclass
class _GatewayProbe:
    """Gateway 对象的探测结果。programmed 是整体条件,listeners 逐个再判。"""

    programmed: bool = False
    address: str = ""
    listeners: list[ListenerRow] = field(default_factory=list)
    reason: str = "NotFound"


@dataclass
class _NodeProbe:
    """节点探测结果。pools / pools_ready 由行派生,不单独探。"""

    rows: list[NodeRow] = field(default_factory=list)
    allocatable_gpu: int = 0
    driver_version: str = ""


_DETAIL_POD_PATTERNS: dict[str, tuple[str, ...]] = {
    "hami": ("hami-",),
    "gpu_operator": ("nvidia-", "gpu-feature-discovery", "node-feature-discovery"),
    "dcgm": ("dcgm",),
    "kata_runtimeclass": ("kata-deploy",),
    "storage": ("topolvm", "csi-cephfs", "rook-ceph"),
    "gateway": ("envoy-",),
    "cert_manager": ("cert-manager",),
    "monitoring": ("prometheus-", "alertmanager-"),
}
_DETAIL_MAX_ROWS = 20
_CERT_MANAGER_GROUP = "cert-manager.io"
_CERT_MANAGER_VERSION = "v1"
_CERTIFICATES_PLURAL = "certificates"


def _pod_not_ready_reason(pod: Any) -> str:
    """返回容器 waiting/terminated 的首个非 Completed 原因,否则取未调度原因。"""
    for cs in (pod.status.container_statuses or []) + (pod.status.init_container_statuses or []):
        state = cs.state
        for sub in (getattr(state, "waiting", None), getattr(state, "terminated", None)):
            reason = str(getattr(sub, "reason", "") or "")
            if reason and reason != "Completed":
                return reason
    for c in pod.status.conditions or []:
        if c.type == "PodScheduled" and c.status != "True":
            return str(getattr(c, "reason", "") or "")
    return ""


def _pod_ready(pod: Any) -> bool:
    return any(c.type == "Ready" and c.status == "True" for c in (pod.status.conditions or []))


_SC_DEFAULT_ANNOTATION = "storageclass.kubernetes.io/is-default-class"


def _selector_text(rc: Any) -> str:
    selector = getattr(getattr(rc, "scheduling", None), "node_selector", None) or {}
    return ", ".join(f"{k}={v}" for k, v in sorted(selector.items()))


def _node_not_ready_reason(node: Any) -> str:
    """Ready 条件为非 True 时的 reason(KubeletNotReady 等);Ready 时留空。"""
    for c in node.status.conditions or []:
        if c.type == "Ready" and c.status != "True":
            return str(getattr(c, "reason", "") or "")
    return ""


def _index_deployment(out: "_Workloads", d: Any) -> None:
    name = str(d.metadata.name or "")
    if name == _HAMI_SCHEDULER:
        out.hami_scheduler = _deploy_row(d)
    if "gpu-operator" in name:
        out.gpu_operator_present = True
        out.gpu_operator_ns = str(d.metadata.namespace or "")
    if "kube-prometheus-stack" in name:
        out.kps_present = True
    if name in _CERT_MANAGER_DEPLOYS:
        out.cert_manager.append(_deploy_row(d))


def _index_daemonset(out: "_Workloads", ds: Any) -> None:
    name = str(ds.metadata.name or "")
    row = _ds_row(ds)
    if name == _HAMI_DEVICE_PLUGIN:
        out.hami_device_plugin = row
    if "dcgm" in name:
        out.dcgm.append(row)
    if "kata-deploy" in name:
        out.kata_deploy = row
    if out.gpu_operator_ns and row.namespace == out.gpu_operator_ns:
        out.gpu_operands.append(row)


def _index_statefulset(out: "_Workloads", st: Any) -> None:
    name = str(st.metadata.name or "")
    if name.startswith("prometheus-"):
        out.kps_present = True
        out.prometheus = _sts_row(st)
    elif name.startswith("alertmanager-"):
        out.alertmanager = _sts_row(st)


def _assemble_probe(
    git_version: str | None,
    w: "_Workloads",
    gw: "_GatewayProbe",
    rcs: list[RuntimeClassRow],
    scs: list[StorageClassRow],
    nodes: "_NodeProbe",
    *,
    error: str | None,
) -> ClusterProbe:
    """将探测行转换为能力快照和组件事实。"""
    pools, pools_ready = health.pool_counts(nodes.rows)
    nodes_ready = sum(pools_ready.values())
    rc_names = {r.name for r in rcs}
    facts = health.build_facts(
        health.ProbeRows(
            nodes=nodes.rows,
            hami_scheduler=w.hami_scheduler,
            hami_device_plugin=w.hami_device_plugin,
            gpu_operands=w.gpu_operands,
            dcgm=w.dcgm,
            cert_manager=w.cert_manager,
            kata_deploy=w.kata_deploy,
            prometheus=w.prometheus,
            alertmanager=w.alertmanager,
            runtime_classes=rcs,
            storage_classes=scs,
            gateway_programmed=gw.programmed,
            gateway_address=gw.address,
            listeners=gw.listeners,
            gateway_reason=gw.reason,
            allocatable_gpu=nodes.allocatable_gpu,
            driver_version=nodes.driver_version,
        ),
        instance_disk_sc=INSTANCE_DISK_STORAGE_CLASS,
        data_disk_sc=DATA_DISK_STORAGE_CLASS,
    )
    return ClusterProbe(
        api_reachable=True,
        k8s_version=git_version,
        distro=derive_distro(git_version),
        hami_ready=w.hami_scheduler.ready > 0,
        dcgm_present=bool(w.dcgm),
        kps_present=w.kps_present,
        gpu_operator_present=w.gpu_operator_present,
        kata_runtimeclass="kata-qemu" in rc_names,
        nvidia_runtimeclass="nvidia" in rc_names,
        gateway_ready=gw.programmed,
        cert_manager_ready=any(d.name == "cert-manager" and d.ready > 0 for d in w.cert_manager),
        nodes_ready=nodes_ready,
        nodes_total=len(nodes.rows),
        storage_classes=tuple(r.name for r in scs),
        pools=pools,
        pools_ready=pools_ready,
        component_facts=facts,
        error=error,
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
        self._timeout = timeout
        self.core = cast(client.CoreV1Api, _TimeoutApi(client.CoreV1Api(), timeout))
        self.net = cast(client.NetworkingV1Api, _TimeoutApi(client.NetworkingV1Api(), timeout))
        self.batch = cast(client.BatchV1Api, _TimeoutApi(client.BatchV1Api(), timeout))
        self.rbac = cast(
            client.RbacAuthorizationV1Api,
            _TimeoutApi(client.RbacAuthorizationV1Api(), timeout),
        )
        self.custom = cast(client.CustomObjectsApi, _TimeoutApi(client.CustomObjectsApi(), timeout))
        probe_client = client.ApiClient()
        self._version = cast(
            client.VersionApi, _TimeoutApi(client.VersionApi(probe_client), timeout)
        )
        self._apps = cast(client.AppsV1Api, _TimeoutApi(client.AppsV1Api(probe_client), timeout))
        self._node = cast(client.NodeV1Api, _TimeoutApi(client.NodeV1Api(probe_client), timeout))
        self._storage = cast(
            client.StorageV1Api, _TimeoutApi(client.StorageV1Api(probe_client), timeout)
        )
        self._executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="k8s")

    async def _run(self, fn: Any, *args: Any) -> Any:
        """在专属线程池执行同步调用;不传播 contextvars。"""
        return await asyncio.get_running_loop().run_in_executor(self._executor, fn, *args)

    async def ensure_namespace(self, namespace: str) -> None:
        await self._run(self._ensure_namespace_sync, namespace)

    def _ensure_namespace_sync(self, namespace: str) -> None:
        labels = {MANAGED_LABEL: "true", **TENANT_NS_PSA_LABELS}
        ns = client.V1Namespace(metadata=client.V1ObjectMeta(name=namespace, labels=labels))
        _create_or_patch(
            lambda: self.core.create_namespace(ns),
            lambda: self.core.patch_namespace(namespace, {"metadata": {"labels": labels}}),
        )
        self._ensure_tenant_rbac_sync(namespace)
        self._ensure_default_netpol_sync(namespace)
        self._ensure_quota_sync(namespace)
        self._ensure_limit_range_sync(namespace)

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

    def _tenant_netpol(self, namespace: str) -> "client.V1NetworkPolicy":
        """租户 NetworkPolicy。入方向:默认拒东西向,放行网关数据面(不限端口)与 SSH 22
        (from 排 Pod 网段,不排私网);出方向:公网除私网/元数据网段,TCP 扣黑名单,
        UDP 白名单 53/443,+ CoreDNS。
        """
        return client.V1NetworkPolicy(
            metadata=client.V1ObjectMeta(name="tenant-default", namespace=namespace),
            spec=client.V1NetworkPolicySpec(
                pod_selector=client.V1LabelSelector(),
                policy_types=["Ingress", "Egress"],
                ingress=[
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
                    client.V1NetworkPolicyIngressRule(
                        _from=self._ssh_ingress_peers(),
                        ports=[client.V1NetworkPolicyPort(protocol="TCP", port=22)],
                    ),
                ],
                egress=[
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
                    client.V1NetworkPolicyEgressRule(
                        to=[
                            client.V1NetworkPolicyPeer(
                                ip_block=client.V1IPBlock(cidr="0.0.0.0/0", _except=PRIVATE_CIDRS)
                            )
                        ],
                        ports=_allowed_tcp_port_ranges(),
                    ),
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

    def _ssh_ingress_peers(self) -> list[Any]:
        """允许 IPv4 SSH 来源但排除配置的 Pod 网段;空配置不设置 except。"""
        cidr = (self.settings.tenant_pod_cidr or "").strip()
        return [
            client.V1NetworkPolicyPeer(
                ip_block=client.V1IPBlock(cidr="0.0.0.0/0", _except=[cidr] if cidr else None)
            )
        ]

    def _ensure_default_netpol_sync(self, namespace: str) -> None:
        policy = self._tenant_netpol(namespace)
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

    async def create_instance(self, spec: InstancePodSpec) -> None:
        await self._run(self._create_instance_sync, spec)

    def _create_instance_sync(self, spec: InstancePodSpec) -> None:
        self._ensure_instance_disk_sync(spec)
        self._ensure_instance_secret_sync(spec)
        self._create_pod_sync(spec)
        self._create_service_sync(spec)
        self._create_httproute_sync(spec)

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
        """核对冲突的 SSH Service;端口相同则跳过,漂移则 patch,删除中则报错。

        分配冲突且同名对象不存在,或 patch 遇分配冲突时抛 NodePortTaken。
        """
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
            return
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
        ports: set[int] = set()
        for svc in self._list_all(self.core.list_service_for_all_namespaces):
            for p in svc.spec.ports or []:
                if p.node_port:
                    ports.add(p.node_port)
        return ports

    @staticmethod
    def batch_container(name: str, image: str, command: list[str], env: list[Any]) -> Any:
        """构造一次性 Job 容器,声明资源请求/上限并应用非 root 安全上下文。"""
        return client.V1Container(
            name=name,
            image=image,
            command=command,
            env=env,
            resources=client.V1ResourceRequirements(
                requests={"cpu": "10m", "memory": "16Mi", "ephemeral-storage": "16Mi"},
                limits={"cpu": "100m", "memory": "64Mi", "ephemeral-storage": "64Mi"},
            ),
            security_context=platform_job_security_context(),
        )

    async def ensure_data_disk(self, namespace: str, name: str, size_gb: int) -> None:
        await self._run(self._ensure_data_disk_sync, namespace, name, size_gb)

    @staticmethod
    def _requested_gi(pvc: Any) -> int:
        """读取整数字符串 Gi 容量;缺失或其它格式返回 0。"""
        spec = getattr(pvc, "spec", None)
        res = getattr(spec, "resources", None) if spec else None
        value = (getattr(res, "requests", None) or {}).get("storage") if res else None
        if isinstance(value, str) and value.endswith("Gi") and value[:-2].isdigit():
            return int(value[:-2])
        return 0

    def _ensure_data_disk_sync(self, namespace: str, name: str, size_gb: int) -> None:
        """创建 CephFS RWX PVC;已有容量不足则申请扩容,不主动缩容。"""
        want = f"{size_gb}Gi"
        existing: Any = _ignore(
            lambda: self.core.read_namespaced_persistent_volume_claim(name, namespace), 404
        )
        if existing is None:
            pvc = client.V1PersistentVolumeClaim(
                metadata=client.V1ObjectMeta(
                    name=name, namespace=namespace, labels={MANAGED_LABEL: "true"}
                ),
                spec=client.V1PersistentVolumeClaimSpec(
                    access_modes=["ReadWriteMany"],
                    storage_class_name=DATA_DISK_STORAGE_CLASS,
                    resources=client.V1VolumeResourceRequirements(requests={"storage": want}),
                ),
            )
            _ignore(
                lambda: self.core.create_namespaced_persistent_volume_claim(namespace, pvc), 409
            )
            return
        if self._requested_gi(existing) >= size_gb:
            return
        self.core.patch_namespaced_persistent_volume_claim(
            name, namespace, {"spec": {"resources": {"requests": {"storage": want}}}}
        )

    async def delete_data_disk(self, namespace: str, name: str) -> None:
        await self._run(self._delete_data_disk_sync, namespace, name)

    def _delete_data_disk_sync(self, namespace: str, name: str) -> None:
        """删数据盘 PVC;SC 的 reclaimPolicy=Delete,CSI 随之销毁 subvolume。
        PVC 不存在或租户 ns 已消失都视为成功(删盘链路幂等)。"""
        _ignore(lambda: self.core.delete_namespaced_persistent_volume_claim(name, namespace), 404)

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
        """GFD 正整数卡数小于 allocatable 时取 GFD;否则有配额的 HAMi 节点报错并计 0。

        其余情况返回 allocatable 的整卡/MIG 份数。
        """
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
        """按节点汇总非 Failed 的受管 Pod GPU 当量并向上取整;跳过未调度 Pod。"""
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

    async def set_node_labels(self, node_name: str, labels: dict[str, str | None]) -> None:
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
            "G": 1000**3,
            "M": 1000**2,
        }
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
        used_by_node = self._used_gpus_by_node()
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

    async def probe_component_detail(self, key: str) -> ComponentDetail:
        return await self._run(self._probe_component_detail_sync, key)

    def _probe_component_detail_sync(self, key: str) -> ComponentDetail:
        if key == "nodes":
            return self._detail_nodes_sync()
        if key == "cert_manager":
            return health.merge_details(
                self._detail_pods_sync(_DETAIL_POD_PATTERNS[key]), self._detail_certificates_sync()
            )
        patterns = _DETAIL_POD_PATTERNS.get(key)
        return self._detail_pods_sync(patterns) if patterns else ComponentDetail()

    def _detail_pods_sync(self, patterns: tuple[str, ...]) -> ComponentDetail:
        """匹配 Pod 的现场状态 + 它们最近的 Warning 事件。未就绪的排前面。"""
        pods = [
            p
            for p in self._list_all(self.core.list_pod_for_all_namespaces)
            if any(pat in (p.metadata.name or "") for pat in patterns)
        ]
        not_ready = [p for p in pods if not _pod_ready(p)]
        shown = (not_ready + [p for p in pods if _pod_ready(p)])[:_DETAIL_MAX_ROWS]
        return ComponentDetail(
            facts=(
                ComponentFact(key="podsTotal", value=str(len(pods))),
                ComponentFact(
                    key="podsNotReady",
                    value=str(len(not_ready)),
                    tone="bad" if not_ready else "normal",
                ),
            ),
            pods=tuple(
                ComponentObject(
                    name=str(p.metadata.name or ""),
                    fields={
                        "namespace": str(p.metadata.namespace or ""),
                        "phase": str(p.status.phase or ""),
                        "node": str(p.spec.node_name or ""),
                        "reason": _pod_not_ready_reason(p),
                        "restarts": str(
                            sum(
                                _safe_int(cs.restart_count)
                                for cs in (p.status.container_statuses or [])
                            )
                        ),
                    },
                )
                for p in shown
            ),
            events=self._detail_events_sync({str(p.metadata.name or "") for p in not_ready}),
        )

    def _detail_events_sync(self, object_names: set[str]) -> tuple[ComponentObject, ...]:
        """未就绪对象的 Warning 事件。需 ClusterRole events 只读(deploy/app/k8s/01-rbac.yaml)。"""
        if not object_names:
            return ()
        events = self._list_all(
            self.core.list_event_for_all_namespaces, field_selector="type=Warning"
        )
        rows = [e for e in events if str(e.involved_object.name or "") in object_names]
        rows.sort(key=lambda e: str(e.last_timestamp or ""), reverse=True)
        return tuple(
            ComponentObject(
                name=str(e.involved_object.name or ""),
                fields={
                    "reason": str(e.reason or ""),
                    "message": str(e.message or "")[:200],
                    "count": str(_safe_int(e.count)),
                    "lastSeen": str(e.last_timestamp or ""),
                },
            )
            for e in rows[:_DETAIL_MAX_ROWS]
        )

    def _detail_nodes_sync(self) -> ComponentDetail:
        """列出存在非 Ready 真值条件或污点的节点,明细按上限截断。"""
        rows: list[ComponentObject] = []
        for node in self._list_all(self.core.list_node):
            pressure = [
                str(c.type)
                for c in (node.status.conditions or [])
                if c.type != "Ready" and c.status == "True"
            ]
            taints = [str(t.key) for t in (node.spec.taints or [])]
            if not pressure and not taints:
                continue
            rows.append(
                ComponentObject(
                    name=str(node.metadata.name or ""),
                    fields={
                        "pressure": ", ".join(pressure),
                        "taints": ", ".join(taints),
                    },
                )
            )
        return ComponentDetail(
            facts=(ComponentFact(key="nodesWithPressure", value=str(len(rows))),),
            pods=tuple(rows[:_DETAIL_MAX_ROWS]),
        )

    def _detail_certificates_sync(self) -> ComponentDetail:
        """cert-manager 证书到期日。需 ClusterRole cert-manager.io/certificates 只读;
        CRD 未装(404)按无证书处理。"""
        try:
            items = self._list_all_custom(
                self.custom.list_cluster_custom_object,
                _CERT_MANAGER_GROUP,
                _CERT_MANAGER_VERSION,
                _CERTIFICATES_PLURAL,
            )
        except client.ApiException as exc:
            if exc.status != 404:
                raise
            return ComponentDetail()
        rows: list[ComponentObject] = []
        soonest = ""
        for cert in items:
            status: dict[str, Any] = cert.get("status") or {}
            not_after = str(status.get("notAfter") or "")
            ready = next(
                (c for c in (status.get("conditions") or []) if c.get("type") == "Ready"), {}
            )
            rows.append(
                ComponentObject(
                    name=str((cert.get("metadata") or {}).get("name") or ""),
                    fields={
                        "namespace": str((cert.get("metadata") or {}).get("namespace") or ""),
                        "ready": str(ready.get("status") or ""),
                        "notAfter": not_after,
                        "reason": str(ready.get("reason") or ""),
                    },
                )
            )
            if not_after and (not soonest or not_after < soonest):
                soonest = not_after
        return ComponentDetail(
            facts=(
                ComponentFact(key="certificates", value=str(len(rows))),
                ComponentFact(key="soonestExpiry", value=soonest),
            ),
            pods=tuple(rows[:_DETAIL_MAX_ROWS]),
        )

    async def probe_cluster(self) -> ClusterProbe:
        return await self._run(self._probe_cluster_sync)

    def _probe_cluster_sync(self) -> ClusterProbe:
        try:
            version: Any = self._version.get_code()
            git_version = getattr(version, "git_version", None)
        except Exception as exc:
            return ClusterProbe(api_reachable=False, error=str(exc))
        errors: list[str] = []

        def step[T](
            label: str, fn: Callable[[], T], default: T, *, ignore: tuple[int, ...] = ()
        ) -> T:
            try:
                return fn()
            except client.ApiException as exc:
                if exc.status not in ignore:
                    errors.append(f"{label}: {exc.status}")
                return default

        workloads = step("apps", self._probe_workloads_sync, _Workloads())
        gateway = step("gateway", self._probe_gateway_sync, _GatewayProbe(), ignore=(404,))
        runtime_classes = step("runtimeclasses", self._runtime_class_rows_sync, [])
        storage_classes = step("storageclasses", self._storage_class_rows_sync, [])
        nodes = step("nodes", self._probe_nodes_sync, _NodeProbe())
        return _assemble_probe(
            git_version,
            workloads,
            gateway,
            runtime_classes,
            storage_classes,
            nodes,
            error="; ".join(errors) or None,
        )

    def _probe_workloads_sync(self) -> "_Workloads":
        """分别列出 Deployment、DaemonSet 和 StatefulSet,按体检项归类。"""
        out = _Workloads()
        deployments: Any = self._apps.list_deployment_for_all_namespaces()
        for d in deployments.items:
            _index_deployment(out, d)
        daemonsets: Any = self._apps.list_daemon_set_for_all_namespaces()
        for ds in daemonsets.items:
            _index_daemonset(out, ds)
        statefulsets: Any = self._apps.list_stateful_set_for_all_namespaces()
        for st in statefulsets.items:
            _index_statefulset(out, st)
        return out

    def _runtime_class_rows_sync(self) -> list[RuntimeClassRow]:
        rcs: Any = self._node.list_runtime_class()
        return [
            RuntimeClassRow(
                name=str(rc.metadata.name or ""),
                handler=str(getattr(rc, "handler", "") or ""),
                node_selector=_selector_text(rc),
            )
            for rc in rcs.items
        ]

    def _storage_class_rows_sync(self) -> list[StorageClassRow]:
        scs: Any = self._storage.list_storage_class()
        return [
            StorageClassRow(
                name=str(sc.metadata.name or ""),
                provisioner=str(getattr(sc, "provisioner", "") or ""),
                binding_mode=str(getattr(sc, "volume_binding_mode", "") or ""),
                expandable=bool(getattr(sc, "allow_volume_expansion", False)),
                reclaim=str(getattr(sc, "reclaim_policy", "") or ""),
                is_default=(sc.metadata.annotations or {}).get(_SC_DEFAULT_ANNOTATION) == "true",
            )
            for sc in scs.items
        ]

    def _probe_gateway_sync(self) -> "_GatewayProbe":
        gw: Any = self.custom.get_namespaced_custom_object(
            GATEWAY_API_GROUP,
            GATEWAY_API_VERSION,
            GATEWAY_NAMESPACE,
            GATEWAY_PLURAL,
            GATEWAY_NAME,
        )
        status: dict[str, Any] = gw.get("status") or {}
        conditions = status.get("conditions") or []
        programmed = any(
            c.get("type") == "Programmed" and c.get("status") == "True" for c in conditions
        )
        addresses = status.get("addresses") or []
        return _GatewayProbe(
            programmed=programmed,
            address=str((addresses[0] or {}).get("value", "")) if addresses else "",
            listeners=_listener_rows(gw),
            reason=next(
                (
                    str(c.get("reason") or "")
                    for c in conditions
                    if c.get("type") == "Programmed" and c.get("status") != "True"
                ),
                "",
            ),
        )

    def _probe_nodes_sync(self) -> "_NodeProbe":
        """节点行 + 可分配卡数 + 驱动版本。只按池标签粗略分组,不走 _list_nodes_sync。"""
        out = _NodeProbe()
        for node in self._list_all(self.core.list_node):
            labels: dict[str, str] = node.metadata.labels or {}
            node_info = getattr(node.status, "node_info", None)
            out.rows.append(
                NodeRow(
                    name=str(node.metadata.name or ""),
                    pool=labels.get(POOL_NODE_LABEL, "unlabeled"),
                    ready=_ready_condition(node),
                    schedulable=not node.spec.unschedulable,
                    kubelet=str(getattr(node_info, "kubelet_version", "") or ""),
                    reason=_node_not_ready_reason(node),
                )
            )
            out.allocatable_gpu += _safe_int((node.status.allocatable or {}).get(_GPU_ALLOCATABLE))
            out.driver_version = out.driver_version or labels.get(_DRIVER_LABEL, "")
        return out

    async def set_node_unschedulable(self, node_name: str, unschedulable: bool) -> None:
        await self._run(self._set_node_unschedulable_sync, node_name, unschedulable)

    def _set_node_unschedulable_sync(self, node_name: str, unschedulable: bool) -> None:
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
            return
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
        """返回首个 Pod 容器 waiting/terminated 失败原因;取不到时返回固定失败摘要。"""
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


def build_prewarm_job(
    platform_namespace: str,
    job_name: str,
    node_name: str,
    image_ref: str,
    image_pull_secret: str | None,
) -> "client.V1Job":
    """预热 Job 对象(纯构造):nodeName 定点、纯拉取触发(命令为 true)、restricted 非 root 上下文;
    镜像缺 sh 由巡检记 failed。"""
    container = RealOrchestrator.batch_container(
        "prewarm", image_ref, ["/bin/sh", "-c", "true"], []
    )
    container.image_pull_policy = "IfNotPresent"
    return client.V1Job(
        metadata=client.V1ObjectMeta(
            name=job_name,
            namespace=platform_namespace,
            labels={PREWARM_LABEL: "true"},
            annotations={"superdl.io/node": node_name, "superdl.io/image": image_ref},
        ),
        spec=client.V1JobSpec(
            backoff_limit=0,
            ttl_seconds_after_finished=600,
            active_deadline_seconds=1800,
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(labels={PREWARM_LABEL: "true"}),
                spec=client.V1PodSpec(
                    node_name=node_name,
                    restart_policy="Never",
                    automount_service_account_token=False,
                    image_pull_secrets=(
                        [client.V1LocalObjectReference(name=image_pull_secret)]
                        if image_pull_secret
                        else None
                    ),
                    tolerations=[client.V1Toleration(operator="Exists")],
                    containers=[container],
                ),
            ),
        ),
    )
