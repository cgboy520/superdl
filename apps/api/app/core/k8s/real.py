"""生产 K8s 编排(kubernetes 官方客户端)。

官方客户端为同步实现,全部调用经专属有界执行器出让事件循环(见 _run)。

对象命名:pod/svc/ingress 同名 = instance uuid;统一打标 superdl.io/instance。
"""

# 本文件需真实集群,单测不覆盖(pyproject [tool.coverage.run] omit 整文件)

import asyncio
import hashlib
import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast

from kubernetes import client, config

from app.core.config import get_settings
from app.core.k8s.base import (
    GPU_MODEL_NODE_LABEL,
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
)
from app.core.logging import get_logger
from app.core.registry import PULL_SECRET_FINGERPRINT_ANNOTATION, PULL_SECRET_NAME

INSTANCE_LABEL = "superdl.io/instance"
PREWARM_LABEL = "superdl.io/prewarm"  # 预热 Job 专用标签,与 managed(实例 Pod 查询)隔离
INGRESS_NAMESPACE = "ingress-nginx"  # Jupyter 北向入口所在 ns(NetworkPolicy 放行来源)

# 租户 ns 的 Pod Security Admission 标签。enforce 只到 baseline:平台镜像以 root 运行,
# restricted 的 runAsNonRoot 会拒绝全部租户 Pod;逃逸面由 kata VM / userns 承担。
# audit/warn 打 restricted,只进审计日志、不挡调度。
TENANT_NS_PSA_LABELS = {
    "pod-security.kubernetes.io/enforce": "baseline",
    "pod-security.kubernetes.io/audit": "restricted",
    "pod-security.kubernetes.io/warn": "restricted",
}


def tenant_security_context() -> "client.V1SecurityContext":
    """租户容器的加固基线:无条件下发,不看 runtimeClass、发行版与档位。

    capabilities / allowPrivilegeEscalation / seccompProfile 是标准 OCI 字段,
    kata-qemu 在 guest 内照常施加;userns 只挡逃逸后在宿主的权限,不替代这一层。
    不下发 runAsNonRoot:平台镜像以 root 运行(ssh root@ + 实例盘挂 /root),
    强开会杀死全部租户 Pod;root 的宿主侧风险由 userns 映射与 kata VM 边界兜住。
    """
    return client.V1SecurityContext(
        allow_privilege_escalation=False,
        capabilities=client.V1Capabilities(drop=["ALL"]),
        seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"),
    )


def _is_conflict(exc: client.ApiException) -> bool:
    return exc.status == 409


def _ignore(fn: Callable[[], Any], *statuses: int) -> Any:
    """执行一次 K8s 调用,吞掉指定 HTTP 状态的 ApiException(幂等语义:create 的 409 =
    已存在,delete/read 的 404 = 不存在),其余照抛。被吞时返回 None。"""
    try:
        return fn()
    except client.ApiException as exc:
        if exc.status not in statuses:
            raise
        return None


def _create_or_patch(create: Callable[[], Any], patch: Callable[[], Any]) -> None:
    """幂等下发:create 撞 409(已存在)即 patch 收敛——加固/标签演进必须覆盖存量对象。"""
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
    """apiserver 拒绝显式 nodePort 的形状:422 + "provided port is already allocated"。

    注意它不等于「端口被别人占」——同名 Service 幂等重建时,分配器先于 AlreadyExists
    命中,于是重放拿到的是 422 而不是 409。占用者是不是自己,须读对象才能判。
    """
    return exc.status == 422 and "already allocated" in str(exc.body or "")


# 租户命名空间兜底配额:主闸是每用户配额,这里留数倍余量,只挡应用侧配额失效时的失控创建。
# 含 cpu/memory/ephemeral 的 Quota 会强制该 ns 所有 Pod 声明对应 request/limit。
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
# 租户容器禁访的内网/元数据网段(Egress 放行公网,黑名单私网)。
# 100.64.0.0/10 = CGNAT,198.18.0.0/15 = 基准测试段,云 metadata 169.254.169.254 含在 169.254.0.0/16。
# IPv6 不入表:未开双栈时默认拒已覆盖,开双栈需在部署侧评审放行策略。
PRIVATE_CIDRS = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "100.64.0.0/10",
    "198.18.0.0/15",
]
# Egress 明确滥用途 TCP 端口黑名单:SMTP 发信(25/465/587)、SMB/NetBIOS(135/139/445)、
# Telnet(23)、RDP(3389)。只封明确滥用途;HTTPS/SSH 出/包管理/对象存储等照常放行。
EGRESS_BLOCKED_TCP_PORTS = (23, 25, 135, 139, 445, 465, 587, 3389)
# 公网 UDP 白名单:53(公网 DNS 兜底,主路径走 CoreDNS)/443(QUIC/HTTP3)。
# 全端口放行的 GPU 机器是一流反射/洪泛源(NTP/DNS/CLDAP 放大、DDoS 代理),
# 其余 UDP 端口按工单白名单逐案评审开放。
EGRESS_ALLOWED_UDP_PORTS = (53, 443)

# 租户容器 ephemeral-storage:只管可写层 + 日志 + emptyDir,镜像只读层不计入。
# request 为调度占位,limit 须宽松(超限即驱逐 Pod),只挡写爆节点盘的滥用。
TENANT_EPHEMERAL_REQUEST = "2Gi"
TENANT_EPHEMERAL_LIMIT = "64Gi"


def _check_subpath(subpath: str) -> None:
    """JuiceFS 子路径只许单段目录名:它会拼进 rm -rf 与 quota --path,拒绝 /、.. 与空串。"""
    if "/" in subpath or ".." in subpath or not subpath:
        raise ValueError(f"illegal juicefs subpath: {subpath!r}")


def _allowed_tcp_port_ranges() -> list["client.V1NetworkPolicyPort"]:
    """1-65535 扣除黑名单端口后的允许区间(endPort 需 K8s 1.21+,Cilium 支持)。"""
    ports: list[client.V1NetworkPolicyPort] = []
    lo = 1
    for blocked in sorted(EGRESS_BLOCKED_TCP_PORTS):
        if lo < blocked:
            ports.append(client.V1NetworkPolicyPort(protocol="TCP", port=lo, end_port=blocked - 1))
        lo = blocked + 1
    ports.append(client.V1NetworkPolicyPort(protocol="TCP", port=lo, end_port=65535))
    return ports


class _TimeoutApi:
    """给官方同步客户端的每次调用注入 `_request_timeout`(客户端无全局超时配置项)。

    包一层而非在各调用点手写,新增调用不会漏。
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


logger = get_logger(__name__)


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
        # cast 保留静态签名检查,运行时是注超时的代理(见 _TimeoutApi)
        self.core = cast(client.CoreV1Api, _TimeoutApi(client.CoreV1Api(), timeout))
        self.net = cast(client.NetworkingV1Api, _TimeoutApi(client.NetworkingV1Api(), timeout))
        self.batch = cast(client.BatchV1Api, _TimeoutApi(client.BatchV1Api(), timeout))
        # K8s 同步调用出让到专属有界执行器:与 bcrypt 等共用的默认执行器隔离,
        # 防集群抖动时慢调用占满默认线程池、卡死登录等无关链路
        self._executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="k8s")

    async def _run(self, fn: Any, *args: Any) -> Any:
        """asyncio.to_thread 的等价物,换专属执行器(run_in_executor 不支持 kwargs)。"""
        return await asyncio.get_running_loop().run_in_executor(self._executor, fn, *args)

    # ---------- namespace ----------

    async def ensure_namespace(self, namespace: str) -> None:
        await self._run(self._ensure_namespace_sync, namespace)

    def _ensure_namespace_sync(self, namespace: str) -> None:
        labels = {MANAGED_LABEL: "true", **TENANT_NS_PSA_LABELS}
        ns = client.V1Namespace(metadata=client.V1ObjectMeta(name=namespace, labels=labels))
        # 既有 ns 也要补标:create 只发生一次,标签演进靠 patch 收敛存量租户
        _create_or_patch(
            lambda: self.core.create_namespace(ns),
            lambda: self.core.patch_namespace(namespace, {"metadata": {"labels": labels}}),
        )
        self._ensure_default_netpol_sync(namespace)
        self._ensure_quota_sync(namespace)
        self._ensure_juicefs_pvc_sync(namespace)

    def _tenant_netpol(self, namespace: str) -> "client.V1NetworkPolicy":
        """入方向:默认拒东西向,放行 Ingress Controller 到 Jupyter(8888)与 SSH(22);
        出方向放行公网(除私网/元数据网段):TCP 扣明确滥用途黑名单,UDP 白名单 53/443,+ DNS。

        SSH 走 NodePort:DNAT 后是否过 NetworkPolicy 取决于 CNI(Cilium 会过,
        kube-proxy iptables 通常不过),显式放行 22 消除对「NodePort 不过策略」的
        隐式依赖;from 不能排私网 —— 跨节点 NodePort 经 SNAT 后来源是节点内网 IP。
        sshd 仅密钥登录,Jupyter(8888)仍只放行 Ingress 来源。
        """
        return client.V1NetworkPolicy(
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
                    ),
                    # SSH NodePort 入流量(见 docstring)
                    client.V1NetworkPolicyIngressRule(
                        _from=[
                            client.V1NetworkPolicyPeer(ip_block=client.V1IPBlock(cidr="0.0.0.0/0"))
                        ],
                        ports=[client.V1NetworkPolicyPort(protocol="TCP", port=22)],
                    ),
                ],
                egress=[
                    # DNS:收敛到 CoreDNS Pod(不放行整个 kube-system 命名空间)
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
                    # 公网 TCP:除私网/元数据网段;端口扣除明确滥用途黑名单
                    client.V1NetworkPolicyEgressRule(
                        to=[
                            client.V1NetworkPolicyPeer(
                                ip_block=client.V1IPBlock(cidr="0.0.0.0/0", _except=PRIVATE_CIDRS)
                            )
                        ],
                        ports=_allowed_tcp_port_ranges(),
                    ),
                    # 公网 UDP 白名单(EGRESS_ALLOWED_UDP_PORTS):DNS/QUIC 之外全拒,
                    # 防反射放大与洪泛代理滥用;特殊协议走工单白名单逐案开放
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
        _ignore(lambda: self.core.create_namespaced_persistent_volume_claim(namespace, pvc), 409)

    # ---------- instance ----------

    async def create_instance(self, spec: InstancePodSpec) -> None:
        await self._run(self._create_instance_sync, spec)

    def _create_instance_sync(self, spec: InstancePodSpec) -> None:
        self._ensure_instance_disk_sync(spec)
        self._ensure_instance_secret_sync(spec)
        self._create_pod_sync(spec)
        self._create_service_sync(spec)
        self._create_ingress_sync(spec)

    # ---------- 平台托管的镜像拉取凭据 ----------

    async def ensure_pull_secret(
        self, namespace: str, dockerconfigjson: str, fingerprint: str
    ) -> None:
        await self._run(self._ensure_pull_secret_sync, namespace, dockerconfigjson, fingerprint)

    def _ensure_pull_secret_sync(
        self, namespace: str, dockerconfigjson: str, fingerprint: str
    ) -> None:
        """superdl-registry-pull:annotation 指纹相同即跳过(每次建 Pod 前都会调用,
        不能次次写 etcd);不同(轮换)则 create/patch 覆写,已建 Pod 不受影响。"""
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
        """per-instance 敏感 env 的 Secret(JUPYTER_TOKEN 等)。

        幂等:已存在则按最新内容 patch(token 轮换/同 uuid 重建收敛);
        生命周期随实例(delete_instance 一并摘除)。
        """
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
        """实例盘 PVC。已存在即跳过,重新开机复用同一只盘,不按新容量重建。
        TopoLVM 为节点本地卷,PV 带 node affinity,首次绑定后 Pod 被拉回原节点。"""
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
        requests = {
            "cpu": str(spec.vcpu),
            "memory": f"{spec.mem_gb}Gi",
            "ephemeral-storage": TENANT_EPHEMERAL_REQUEST,
            **spec.gpu_resources,
        }
        limits = {**requests, "ephemeral-storage": TENANT_EPHEMERAL_LIMIT}
        env = [client.V1EnvVar(name=k, value=v) for k, v in spec.env.items()]
        # 敏感值只以 secretKeyRef 引用 per-instance Secret,明文不进 Pod spec
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
                # 平台托管的 Harbor 拉取凭据(未配机器人 = 项目 public,不引用)
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
                        ports=[
                            client.V1ContainerPort(container_port=22, name="ssh"),
                            client.V1ContainerPort(container_port=8888, name="jupyter"),
                        ],
                        volume_mounts=mounts,
                        security_context=tenant_security_context(),
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
            # 409 不当幂等成功:同名对象 Terminating 时也是 409,新 Pod 并未创建
            existing: Any = self.core.read_namespaced_pod(spec.name, spec.namespace)
            if existing.metadata.deletion_timestamp is not None:
                raise RuntimeError(
                    f"pod {spec.name} is terminating; create must wait for it to disappear"
                ) from exc

    def _create_service_sync(self, spec: InstancePodSpec) -> None:
        """SSH 走 NodePort(显式端口),Jupyter 走 ClusterIP(Ingress 回源)。

        拆成两个 Service:type=NodePort 会给每个 port 都分配 NodePort,合并会让
        Jupyter 从 30000–32767 随机取号,撞 SSH 端口池。
        """
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
            # 409(同名对象已存在)与 422(nodePort 分配器撞车)都可能是自己的幂等重放,
            # 一律先读对象核对,由 _reconcile_ssh_service_conflict_sync 判幂等成功还是真被占
            if not (_is_conflict(exc) or _is_node_port_taken(exc)):
                raise
            self._reconcile_ssh_service_conflict_sync(spec, exc)
        _ignore(lambda: self.core.create_namespaced_service(spec.namespace, jupyter_svc), 409)

    def _reconcile_ssh_service_conflict_sync(
        self, spec: InstancePodSpec, create_exc: "client.ApiException"
    ) -> None:
        """SSH Service 创建冲突(409/422)的核对:同名对象存在 ≠ 幂等成功,nodePort 必须
        与期望一致(写法对照 _create_pod_sync 的 deletion_timestamp 核对)。漂移则 patch 回期望端口。

        422 走到这里是因为分配器先于 AlreadyExists 命中:同名 Service 不存在才说明
        端口真被集群其它对象占用,那时才归一化成 NodePortTaken 交编排层换端口。
        """
        try:
            existing: Any = self.core.read_namespaced_service(spec.name, spec.namespace)
        except client.ApiException as read_exc:
            if read_exc.status == 404 and _is_node_port_taken(create_exc):
                raise NodePortTaken(spec.ssh_node_port) from create_exc
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
            # 期望端口已被集群其它对象占用:同样归一化成交编排层换端口
            if _is_node_port_taken(patch_exc):
                raise NodePortTaken(spec.ssh_node_port) from patch_exc
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
                                            name=jupyter_service_name(spec.name),
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
        _ignore(lambda: self.net.create_namespaced_ingress(spec.namespace, ingress), 409)

    async def delete_instance(self, namespace: str, name: str, *, force: bool = False) -> None:
        await self._run(self._delete_instance_sync, namespace, name, force)

    def _delete_instance_sync(self, namespace: str, name: str, force: bool = False) -> None:
        # 节点失联时 kubelet 确认不了删除,Pod 无限期 Terminating;强删(grace 0)直接摘对象
        pod_kwargs = {"grace_period_seconds": 0} if force else {}
        for deleter in (
            lambda: self.core.delete_namespaced_pod(name, namespace, **pod_kwargs),
            lambda: self.core.delete_namespaced_service(name, namespace),
            lambda: self.core.delete_namespaced_service(jupyter_service_name(name), namespace),
            lambda: self.core.delete_namespaced_secret(instance_env_secret_name(name), namespace),
            lambda: self.net.delete_namespaced_ingress(name, namespace),
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

    async def read_instance_logs(
        self, namespace: str, name: str, *, tail_lines: int, since_seconds: int | None = None
    ) -> str:
        return await self._run(
            self._read_instance_logs_sync, namespace, name, tail_lines, since_seconds
        )

    def _read_instance_logs_sync(
        self, namespace: str, name: str, tail_lines: int, since_seconds: int | None
    ) -> str:
        # 用户在线等日志:覆盖默认读超时收紧到 5s(_TimeoutApi 只 setdefault,显式传参生效)
        kwargs: dict[str, Any] = {
            "container": "workspace",
            "tail_lines": tail_lines,
            "timestamps": True,
            "_request_timeout": (5.0, 5.0),
        }
        if since_seconds is not None:
            kwargs["since_seconds"] = since_seconds
        return cast(str, self.core.read_namespaced_pod_log(name, namespace, **kwargs))

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
            # jupyter 副名归并到实例名(<uuid>-jupyter → <uuid>)
            out.add((ns, name[: -len("-jupyter")] if name.endswith("-jupyter") else name))
        for ing in self._list_all(
            self.net.list_ingress_for_all_namespaces, label_selector=MANAGED_LABEL
        ):
            ns = ing.metadata.namespace
            if ns.startswith(prefix):
                out.add((ns, ing.metadata.name))
        return sorted(out)

    async def used_node_ports(self) -> set[int]:
        return await self._run(self._used_node_ports_sync)

    def _used_node_ports_sync(self) -> set[int]:
        # 必须列出全集群 Service 的 NodePort,不能按 MANAGED_LABEL 过滤:
        # blocked 端口标的就是「被非平台对象占用」的端口,复检看不见占用者会
        # 每 30 秒把真占用误放回池 → 再撞 → 再封,振荡并把新建实例推过创建超时
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
        """受管 Job 生命周期(幂等):已成功 → 清理并返回;进行中 → 抛错交 outbox 退避重试;
        失败 → 删 Job 重建;不存在 → 创建并抛错等下轮。wipe/quota 共用
        (预热 Job 创建后不等完成,由巡检收敛,见 _prewarm_image_sync)。"""
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
        job = client.V1Job(
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
                        containers=[container],
                        volumes=volumes,
                    ),
                ),
            ),
        )
        _ignore(lambda: self.batch.create_namespaced_job(namespace, job), 409)
        raise RuntimeError(f"job created, awaiting completion: {job_name}")

    @staticmethod
    def _batch_container(name: str, image: str, command: list[str], env: list[Any]) -> Any:
        """一次性 Job 容器基座(wipe/quota/prewarm 共用):资源声明 + 租户同款安全上下文。"""
        return client.V1Container(
            name=name,
            image=image,
            command=command,
            env=env,
            # 租户 ns 的 ResourceQuota 含 cpu/memory/ephemeral 硬限,
            # 不声明 request/limit 的 Pod 会被配额准入直接拒绝
            resources=client.V1ResourceRequirements(
                requests={"cpu": "10m", "memory": "16Mi", "ephemeral-storage": "16Mi"},
                limits={"cpu": "100m", "memory": "64Mi", "ephemeral-storage": "64Mi"},
            ),
            security_context=tenant_security_context(),
        )

    async def wipe_disk(self, namespace: str, subpath: str) -> None:
        await self._run(self._wipe_disk_sync, namespace, subpath)

    def _wipe_disk_sync(self, namespace: str, subpath: str) -> None:
        """租户 ns 内起 Job 挂 JuiceFS PVC 删除子目录。幂等:见 _run_managed_job_sync。"""
        _check_subpath(subpath)
        container = self._batch_container(
            "wipe", "busybox:1.36", ["rm", "-rf", f"/data/{subpath}"], env=[]
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

    async def set_disk_quota(self, subpath: str, capacity_gb: int) -> None:
        await self._run(self._disk_quota_sync, subpath, capacity_gb, True)

    async def delete_disk_quota(self, subpath: str) -> None:
        await self._run(self._disk_quota_sync, subpath, 0, False)

    def _disk_quota_sync(self, subpath: str, capacity_gb: int, is_set: bool) -> None:
        """平台 ns 起 juicefs CLI Job 下发/摘除目录配额。幂等(见 _run_managed_job_sync)。
        metaurl 经 secretKeyRef 注入(superdl-api-secrets 与 Job 同 ns),worker 零接触明文;
        subpath/capacity 走 env 间接引用,不进 shell 命令串(防注入)。
        密码不进 argv:shell 内把 metaurl 拆成「无密码 URL(argv)+ META_PASSWORD(env)」,
        juicefs v1.0+ 官方机制;否则全租户共享文件系统的元数据引擎凭据会出现在
        /proc/<pid>/cmdline(节点上任何进程可读)。"""
        _check_subpath(subpath)
        # 密码拆分在容器内 shell 完成(env 不进 /proc cmdline);metaurl 密码段约定不含 @
        split = (
            'export META_PASSWORD="$(printf \'%s\' "$JUICEFS_METAURL"'
            " | sed -n 's|^[^:]*://[^:]*:\\([^@]*\\)@.*|\\1|p')\"; "
            'METAURL_NOPASS="$(printf \'%s\' "$JUICEFS_METAURL"'
            " | sed 's|^\\([^:]*://[^:]*\\):[^@]*@|\\1@|')\"; "
        )
        if is_set:
            script = (
                split + 'juicefs quota set "$METAURL_NOPASS" --path "/$QUOTA_SUBPATH"'
                ' --capacity "$QUOTA_CAPACITY_GB" --create'
            )
        else:
            # 删盘链路:无配额记录(存量盘/从未下发成功)不算失败,目录随后由 wipe Job 擦除
            script = (
                split + 'juicefs quota delete "$METAURL_NOPASS" --path "/$QUOTA_SUBPATH" || true'
            )
        container = self._batch_container(
            "quota", self.settings.juicefs_cli_image, ["sh", "-c", script], env=[]
        )
        container.env = [
            client.V1EnvVar(
                name="JUICEFS_METAURL",
                value_from=client.V1EnvVarSource(
                    secret_key_ref=client.V1SecretKeySelector(
                        name="superdl-api-secrets", key="juicefs-metaurl"
                    )
                ),
            ),
            client.V1EnvVar(name="QUOTA_SUBPATH", value=subpath),
            client.V1EnvVar(name="QUOTA_CAPACITY_GB", value=str(capacity_gb)),
        ]
        action = "set" if is_set else "del"
        self._run_managed_job_sync(
            self.settings.k8s_platform_namespace,
            f"quota-{action}-{subpath[-36:]}".lower(),
            container,
            volumes=[],
            # NetworkPolicy jobs-egress 按此标签放行 PG(JuiceFS 元数据引擎)出向
            pod_labels={"app": "superdl-disk-quota"},
        )

    # ---------- 节点 ----------

    @staticmethod
    def _gpu_amount(resources: dict[str, Any] | None) -> int:
        """整卡 + MIG 分片统一计数(HAMi 共享池的 nvidia.com/gpu 为虚拟化后份额)。"""
        total = 0
        for key, value in (resources or {}).items():
            if key == "nvidia.com/gpu" or key.startswith("nvidia.com/mig-"):
                total += int(value)
        return total

    @staticmethod
    def _physical_gpu_amount(node: Any) -> int:
        """节点物理卡数。HAMi device-plugin 把 allocatable nvidia.com/gpu 放大为
        物理 × deviceSplitCount(默认 10):物理口径以 GFD 标签 nvidia.com/gpu.count 为准;
        无该标签(未切分池)按 allocatable 原样。
        hami 池(切分池)缺 GFD 标签属异常(GFD 未上报/标签被清):按 allocatable 原样会
        把物理卡数虚高一个数量级直接超卖——拒纳管(计 0)并告警,待 GFD 恢复自动回归。"""
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
        """Pod 占用的物理卡当量:HAMi 份额按 gpucores 折算(N 虚卡 × X% = N·X/100 物理卡);
        整卡/MIG 按 1/个。台账与库存统一物理口径,否则共享池被放大一个数量级。"""
        whole = RealOrchestrator._gpu_amount(limits)
        cores = limits.get("nvidia.com/gpucores")
        if cores is not None and str(cores).isdigit() and whole > 0:
            return whole * int(cores) / 100.0
        return float(whole)

    @staticmethod
    def _list_all(list_fn: Any, **kwargs: Any) -> list[Any]:
        """分页拉满全量:官方客户端默认不翻页,对象超过单页上限会被静默截断。"""
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

    def _used_gpus_by_node(self) -> dict[str, int]:
        """全部受管 Pod 一次拉取,按节点聚合已用份额(物理卡当量:整卡/MIG 按 1,
        HAMi 按 gpucores 折算后向上取整,不低估占用)。未调度的 Pod 无节点可归,跳过。"""
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
        # 必须长后缀优先("Ki" 先于 "K"):显式按键长降序,不依赖 dict 插入顺序
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
                )
            )
        return out

    async def probe_cluster(self) -> ClusterProbe:
        return await self._run(self._probe_cluster_sync)

    def _probe_cluster_sync(self) -> ClusterProbe:
        # 裸客户端同样经 _TimeoutApi 注超时:探测挂在慢集群上会把巡检整轮拖死。
        # 版本失败 = API 不可达,整体判不可用;组件清点逐项容错(RBAC 缺项不清零全局)
        try:
            version: Any = _TimeoutApi(client.VersionApi(), self._timeout).get_code()
            git_version = getattr(version, "git_version", None)
        except Exception as exc:
            return ClusterProbe(api_reachable=False, error=str(exc))
        errors: list[str] = []
        apps = _TimeoutApi(client.AppsV1Api(), self._timeout)
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
            rcs: Any = _TimeoutApi(client.NodeV1Api(), self._timeout).list_runtime_class()
            runtime_classes = tuple(rc.metadata.name for rc in rcs.items)
        except client.ApiException as exc:
            errors.append(f"runtimeclasses: {exc.status}")
        storage_classes: tuple[str, ...] = ()
        try:
            scs: Any = _TimeoutApi(client.StorageV1Api(), self._timeout).list_storage_class()
            storage_classes = tuple(sc.metadata.name for sc in scs.items)
        except client.ApiException as exc:
            errors.append(f"storageclasses: {exc.status}")
        pools: dict[str, int] = {}
        try:
            # 只数池标签,不走 _list_nodes_sync(它还会全量 LIST Pod 算已用份额,探测用不上;
            # 节点巡检同一轮紧接着就会 list_nodes)
            for node in self._list_all(self.core.list_node):
                key = (node.metadata.labels or {}).get(POOL_NODE_LABEL, "unlabeled")
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
            pools=pools,
            error="; ".join(errors) or None,
        )

    async def set_node_unschedulable(self, node_name: str, unschedulable: bool) -> None:
        await self._run(self._set_node_unschedulable_sync, node_name, unschedulable)

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

    async def prewarm_image(
        self, node_name: str, image_ref: str, *, image_pull_secret: str | None = None
    ) -> None:
        await self._run(self._prewarm_image_sync, node_name, image_ref, image_pull_secret)

    def _prewarm_image_sync(
        self, node_name: str, image_ref: str, image_pull_secret: str | None
    ) -> None:
        """nodeName 定点起拉取 Job,创建后即返回(不等待,大镜像拉取可达数十分钟,
        完成态由 prewarm_patrol 巡检经 get_prewarm_status 收敛)。已存在同名 Job 则跳过。"""
        job_name = self._prewarm_job_name(node_name, image_ref)
        if _ignore(
            lambda: self.batch.read_namespaced_job(job_name, self.settings.k8s_platform_namespace),
            404,
        ):
            return  # 幂等:任意状态的既有 Job 都交巡检收敛
        # 平台镜像均含 sh;缺 sh 会 StartError,由巡检记 failed
        container = self._batch_container("prewarm", image_ref, ["/bin/sh", "-c", "true"], env=[])
        # Always:预热 Job 的职责就是「让节点缓存等于当前 image_ref」。平台镜像允许同名 tag 重推
        # (见 deploy/instance-images/README.md),IfNotPresent 会让节点停在旧 digest;
        # Always 只多一次 manifest 校验,digest 没变不重传层。
        # 实例 Pod 仍是 IfNotPresent —— 开机不依赖仓库可达。
        container.image_pull_policy = "Always"
        job = client.V1Job(
            metadata=client.V1ObjectMeta(
                name=job_name,
                namespace=self.settings.k8s_platform_namespace,
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
                        # 容忍一切污点:预热须覆盖 cordon/维护中的节点
                        tolerations=[client.V1Toleration(operator="Exists")],
                        containers=[container],
                    ),
                ),
            ),
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
        """失败原因优先取 Pod 容器态(ErrImagePull 等),兜底 Job condition。"""
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
