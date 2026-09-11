"""K8s 编排抽象:orchestrator 只面向本协议编程,dev/test 用 Fake,生产用 Real;业务代码禁止直接
import kubernetes 客户端。"""

from dataclasses import dataclass, field
from typing import Protocol

# StorageClass 名,与 deploy/cluster/values/{topolvm,juicefs}.yaml 一致;下发门禁按名核对
INSTANCE_DISK_STORAGE_CLASS = "topolvm-provisioner"  # 实例盘:节点本地 NVMe LV
JUICEFS_STORAGE_CLASS = "superdl-juicefs"  # 数据盘:JuiceFS 共享后端
JUICEFS_PVC_NAME = "juicefs-shared"  # 每租户 ns 一只共享 PVC(数据盘按 subPath 切分)

# 北向入口坐标,与 deploy/app/k8s/04-gateway.yaml 的 Gateway 逐字一致
GATEWAY_NAMESPACE = "superdl"  # Gateway 对象所在 ns(= 平台自身 ns)
GATEWAY_NAME = "superdl"
# 租户 Jupyter listener(*.app.<域名>),唯一开 allowedRoutes Selector 的
GATEWAY_APP_LISTENER = "app-https"
# 服务端点 listener(*.svc.<域名>),只有它挂 SecurityPolicy.extAuth
GATEWAY_SVC_LISTENER = "svc-https"
# Gateway API 资源坐标(CustomObjectsApi)
GATEWAY_API_GROUP = "gateway.networking.k8s.io"
GATEWAY_API_VERSION = "v1"
HTTPROUTE_PLURAL = "httproutes"
GATEWAY_PLURAL = "gateways"


def jupyter_service_name(instance_name: str) -> str:
    """Jupyter 的 ClusterIP Service 名(与 SSH NodePort Service 分开)。"""
    return f"{instance_name}-jupyter"


def service_endpoint_service_name(instance_name: str) -> str:
    """服务端点的 ClusterIP Service 名(与 SSH / Jupyter Service 分开)。"""
    return f"{instance_name}-svc"


def instance_disk_pvc_name(instance_name: str) -> str:
    """实例盘 PVC 名。平台自管生命周期,只在释放/回收时删,不用 Pod 拥有的 ephemeral volume。"""
    return f"{instance_name}-root"


def instance_env_secret_name(instance_name: str) -> str:
    """per-instance 敏感 env 的 Secret 名(JUPYTER_TOKEN 等),随实例删除。"""
    return f"jupyter-{instance_name}"


@dataclass(frozen=True)
class InstancePodSpec:
    """创建一个租户实例所需的全部 K8s 参数(由 orchestrator + gpu_adapter 产出)。"""

    namespace: str
    name: str  # pod/svc/ingress 同名,= instance uuid
    image: str
    gpu_resources: dict[
        str, str
    ]  # 资源请求,如 {"nvidia.com/gpu": "1", "nvidia.com/gpucores": "50"}
    runtime_class: str | None  # kata-qemu(整卡)/ None(runc)
    host_users: bool  # False = 开 user namespaces(共享池加固)
    vcpu: int
    mem_gb: int
    disk_gb: int
    # NodePort;None = 不开 SSH
    ssh_node_port: int | None
    jupyter_host: str  # <uuid>.app.<域名>,HTTPRoute hostname
    env: dict[str, str] = field(default_factory=dict)  # 非敏感环境变量
    # 敏感环境变量(JUPYTER_TOKEN 等):写 per-instance Secret,Pod 以 secretKeyRef 引用
    secret_env: dict[str, str] = field(default_factory=dict)
    authorized_keys: tuple[str, ...] = ()
    node_selector: dict[str, str] = field(default_factory=dict)  # 池标签
    data_disk_subpath: str | None = None  # JuiceFS 子路径(挂 /root/data)
    scheduler_name: str | None = None  # 指定调度器(HAMi 池 = hami-scheduler)
    annotations: dict[str, str] = field(default_factory=dict)  # 如 HAMi use-gputype
    # 镜像拉取凭据 Secret 名(core/registry.PULL_SECRET_NAME);None = 不引用
    image_pull_secret: str | None = None

    # 服务型实例专用;dev 形态全取默认。Never = 容器退出即终态;Always = kubelet 原地重启容器
    restart_policy: str = "Never"
    command: tuple[str, ...] | None = None  # 覆盖镜像 ENTRYPOINT;None = 用镜像自带
    args: tuple[str, ...] | None = None
    service_port: int | None = None  # 非空 → 建 <name>-svc ClusterIP + 服务 HTTPRoute
    service_host: str | None = None  # <slug>.svc.<域名>,服务 HTTPRoute 的 hostname
    # 非空 → 挂 readinessProbe + startupProbe(httpGet)
    health_path: str | None = None
    # False → 不建 SSH NodePort Service
    with_ssh: bool = True


class NodePortTaken(Exception):
    """请求的 NodePort 已被其它对象占用(apiserver 422);调用方标 blocked 并换端口。"""

    def __init__(self, port: int) -> None:
        super().__init__(f"node port {port} already allocated")
        self.port = port


@dataclass(frozen=True)
class PodStatus:
    """Pod 状态(get_status 与 list_instance_pods 同形状);LIST 条目带 namespace/name 与 labels
    (泄漏回收据 labels 豁免受管 Job 的子 Pod)。"""

    exists: bool
    ready: bool = False
    phase: str = "Unknown"  # Pending / Running / Succeeded / Failed / Unknown
    node_name: str | None = None
    # deletionTimestamp 已设 = Terminating(phase 仍 Running),判活看这个字段
    deleting: bool = False
    namespace: str = ""
    name: str = ""
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ClusterProbe:
    """集群能力探测快照(nodes 巡检落 cluster_status 表,门禁与集群页读表不实时探测)。"""

    api_reachable: bool
    k8s_version: str | None = None  # gitVersion 原文,如 v1.36.2+rke2r1
    distro: str | None = None  # rke2 / k3s / None=未知(derive_distro)
    hami_ready: bool = False  # hami-scheduler Deployment ready≥1
    dcgm_present: bool = False
    kps_present: bool = False
    gpu_operator_present: bool = False
    kata_runtimeclass: bool = False  # RuntimeClass kata-qemu 存在
    nvidia_runtimeclass: bool = False  # RuntimeClass nvidia 存在(k3s 上 shared 档下发的前提)
    # Gateway 对象 status.conditions 的 Programmed=True
    gateway_ready: bool = False
    cert_manager_ready: bool = False  # cert-manager ready≥1(泛域名证书签发与续期)
    nodes_ready: int = 0  # Ready 且可调度的节点数
    nodes_total: int = 0  # 集群节点总数(含未打池标签)
    storage_classes: tuple[str, ...] = ()
    pools: dict[str, int] = field(default_factory=dict)  # 池→节点数,未打标计 unlabeled
    error: str | None = None


def derive_distro(git_version: str | None) -> str | None:
    """gitVersion 后缀派生发行版;识别不出返回 None。"""
    if not git_version:
        return None
    if "+rke2" in git_version:
        return "rke2"
    if "+k3s" in git_version:
        return "k3s"
    return None


@dataclass(frozen=True)
class PrewarmJobStatus:
    """镜像预热 Job 状态。"""

    state: str  # absent / running / succeeded / failed
    message: str | None = None  # 失败原因(Job condition / Pod waiting reason)


class K8sOrchestrator(Protocol):
    """全部操作必须幂等(outbox at-least-once 语义)。"""

    async def ensure_namespace(self, namespace: str) -> None:
        """创建租户 namespace + 默认拒东西向 NetworkPolicy + ResourceQuota。已存在则跳过。"""
        ...

    async def ensure_pull_secret(
        self, namespace: str, dockerconfigjson: str, fingerprint: str
    ) -> None:
        """在 namespace 写/覆写镜像拉取 Secret(dockerconfigjson,core/registry.PULL_SECRET_NAME);
        annotation 指纹相同即跳过。幂等。"""
        ...

    async def create_instance(self, spec: InstancePodSpec) -> None:
        """创建 Pod + Service + HTTPRoute,已存在则跳过。dev 建 SSH NodePort + Jupyter ClusterIP +
        Jupyter HTTPRoute;service 建(with_ssh 时 SSH)+ <name>-svc ClusterIP + 服务 HTTPRoute。"""
        ...

    async def delete_instance(self, namespace: str, name: str, *, force: bool = False) -> None:
        """删除该实例的 Pod/Service/HTTPRoute(两种形态的对象都试删,不存在即跳过);不动实例盘。
        force=True 强删(gracePeriodSeconds=0),只在节点失联时用。"""
        ...

    async def delete_instance_disk(self, namespace: str, name: str) -> None:
        """删除实例盘 PVC。只在实例终结(释放/回收/creating 超时)时调用,关机、重启、pod_lost 不许调;
        调用点须先确认 Pod 已消失。不存在则跳过。"""
        ...

    async def get_status(self, namespace: str, name: str) -> PodStatus: ...

    async def read_instance_logs(self, namespace: str, name: str, *, tail_lines: int) -> str:
        """读取实例容器日志末尾 tail_lines 行(请求路径直读的唯一例外);调用方自行做鉴权与限流。"""
        ...

    async def list_instance_pods(self) -> list[PodStatus]:
        """全量列出租户实例 Pod 状态(reconciler 每轮一次,替代逐实例 get_status)。"""
        ...

    async def list_instance_endpoints(self) -> list[tuple[str, str]]:
        """列出全部租户实例的 Service/HTTPRoute (namespace, 实例名),副名已归并;孤儿端点清理用。"""
        ...

    async def used_node_ports(self) -> set[int]:
        """集群内受管 Service 当前占用的 NodePort 集合。blocked 端口复检用。"""
        ...

    async def wipe_disk(self, namespace: str, subpath: str) -> None:
        """擦除数据盘的 JuiceFS 子路径(集群侧 Job)。幂等;未完成时抛异常交 outbox 重试。"""
        ...

    async def set_disk_quota(self, namespace: str, subpath: str, capacity_gb: int) -> None:
        """下发 JuiceFS 目录硬配额(平台 ns 的 CLI Job)。幂等;进行中/失败抛异常交 outbox 重试。
        namespace 用于定位该租户共享 PVC 的 PV 子目录前缀。"""
        ...

    async def delete_disk_quota(self, namespace: str, subpath: str) -> None:
        """删盘前摘除目录配额(无配额记录视为成功)。幂等;失败抛异常。"""
        ...

    async def list_nodes(self, include_unlabeled: bool = False) -> list["NodeInfo"]:
        """节点视图;默认仅带 superdl.io/pool 标签的节点,include_unlabeled=True 含未打标节点。"""
        ...

    async def set_node_labels(self, node_name: str, labels: dict[str, str]) -> None:
        """merge-patch 节点 labels(巡检收敛 superdl.io/gpu-model 用)。幂等。"""
        ...

    async def prewarm_image(
        self, node_name: str, image_ref: str, *, image_pull_secret: str | None = None
    ) -> None:
        """在指定节点创建镜像预热 Job,创建即返回(完成态由巡检收敛);同名已存在则跳过。"""
        ...

    async def get_prewarm_status(self, node_name: str, image_ref: str) -> "PrewarmJobStatus":
        """查询该(节点,镜像)预热 Job 状态。"""
        ...

    async def delete_prewarm_job(self, node_name: str, image_ref: str) -> None:
        """清理预热 Job(收敛后回收;不存在则跳过)。"""
        ...

    async def probe_cluster(self) -> "ClusterProbe":
        """只读能力探测:版本/发行版/组件存在性/RuntimeClass/StorageClass/池分布。"""
        ...

    async def set_node_unschedulable(self, node_name: str, unschedulable: bool) -> None:
        """cordon(True)/uncordon(False)。幂等:重复设置同值无副作用。"""
        ...

    async def delete_node(self, node_name: str) -> None:
        """节点退役:先 cordon 再删 Node 对象;节点已不存在视为成功。不吊销 kubelet 证书
        (控制面侧动作,见 nodes 模块 runbook)。"""
        ...


GPU_MODEL_NODE_LABEL = (
    "superdl.io/gpu-model"  # 平台 canonical 型号标签(巡检写入,调度 nodeSelector 依赖)
)
POOL_NODE_LABEL = "superdl.io/pool"  # 节点池标签(装机时定死;kata / hami / mig 分池铁律)
# 平台受管对象标签:实例 Pod/Service/HTTPRoute/受管 Job/租户 ns 均打;兼作 Gateway listener 的
# allowedRoutes Selector
MANAGED_LABEL = "superdl.io/managed"
# Job 控制器打在子 Pod 上的标签:泄漏回收的豁免依据
JOB_NAME_LABEL = "batch.kubernetes.io/job-name"


@dataclass(frozen=True)
class NodeInfo:
    """管理端节点视图。"""

    name: str
    pool_label: str
    gpu_total: int
    gpu_used: int
    status: str  # Ready / NotReady / Cordoned
    # 节点物理规格(node.status.capacity;0 = 未上报)
    vcpu: int = 0
    mem_gb: int = 0
    disk_gb: int = 0
    # GFD 型号原文标签(nvidia.com/gpu.product)与平台 canonical 标签当前值(空串=无)
    gpu_model_label: str = ""
    model_label_current: str = ""
    # GFD 驱动/CUDA 版本(nvidia.com/cuda.{driver,runtime}-version.full;空串=无)
    driver_version_label: str = ""
    cuda_version_label: str = ""
