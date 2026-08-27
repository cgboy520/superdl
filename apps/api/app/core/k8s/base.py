"""K8s 编排抽象:orchestrator 只面向本协议编程,dev/test 用 Fake,生产用 Real。

业务代码禁止直接 import kubernetes 客户端。
"""

from dataclasses import dataclass, field
from typing import Protocol

# 存储契约:三个名字须与 deploy/cluster/values/{topolvm,juicefs}.yaml 建出的 StorageClass 一致
# (K8s 建 PVC 不校验 SC 存在,名字错了 PVC 永久 Pending)。
# 下发门禁 nodes.require_storage_classes 按名核对已探测到的 SC。
INSTANCE_DISK_STORAGE_CLASS = "topolvm-provisioner"  # 实例盘:节点本地 NVMe LV
JUICEFS_STORAGE_CLASS = "superdl-juicefs"  # 数据盘:JuiceFS 共享后端
JUICEFS_PVC_NAME = "juicefs-shared"  # 每租户 ns 一只共享 PVC(数据盘按 subPath 切分)

# 北向入口契约:三个名字须与 deploy/app/k8s/04-gateway.yaml 里的 Gateway 逐字一致。
# 与 StorageClass 同一类问题——写错不报错:HTTPRoute 会一直停在
# status.parents[].conditions 的 Accepted=False / NotAllowedByListeners,
# 而 create 调用本身返回 201,实例照常进 running,只是 Jupyter 域名永远 404。
# 这条链路没有下发门禁(入口不通不影响实例本身与计费),兜底在管理端集群体检的
# gateway 项:判据是 Gateway 对象的 Programmed 条件,见 probe_cluster。
GATEWAY_NAMESPACE = "superdl"  # Gateway 对象所在 ns(= 平台自身 ns)
GATEWAY_NAME = "superdl"
# 租户 Jupyter 专用 listener(*.app.<域名>)。平台自身三个入口挂在各自的 listener 上,
# 租户路由只许挂这一个:它是唯一开了 allowedRoutes.namespaces.from=Selector 的。
GATEWAY_APP_LISTENER = "app-https"
# 服务型实例的对外端点 listener(*.svc.<域名>)。与 app-https 分成两个 listener 是刻意的:
# 只有这一个挂 SecurityPolicy.extAuth(API Key 鉴权)。同 listener 就没法用 hostname 把
# 两类流量分开,只能退化成逐路由挂策略 —— 对象数从 O(1) 变成 O(端点数)。
GATEWAY_SVC_LISTENER = "svc-https"
# Gateway API 资源坐标(官方客户端无 typed model,一律走 CustomObjectsApi)
GATEWAY_API_GROUP = "gateway.networking.k8s.io"
GATEWAY_API_VERSION = "v1"
HTTPROUTE_PLURAL = "httproutes"
GATEWAY_PLURAL = "gateways"


def jupyter_service_name(instance_name: str) -> str:
    """Jupyter 的 ClusterIP Service 名,与 SSH 的 NodePort Service 分开。

    合成一个 type=NodePort Service 时 K8s 会给 Jupyter 也随机分配 NodePort,撞 SSH 端口池。
    """
    return f"{instance_name}-jupyter"


def service_endpoint_service_name(instance_name: str) -> str:
    """服务型实例的 ClusterIP Service 名(网关回源目标)。

    与 SSH(NodePort,同名于实例)和 Jupyter(<name>-jupyter)三者分开:
    合成一个 type=NodePort Service 会让 K8s 给每个 port 都分配 NodePort,撞 SSH 端口池。
    """
    return f"{instance_name}-svc"


def instance_disk_pvc_name(instance_name: str) -> str:
    """实例盘 PVC 名。平台自管生命周期,只在释放/回收时删,不用 Pod 拥有的 ephemeral volume。"""
    return f"{instance_name}-root"


def instance_env_secret_name(instance_name: str) -> str:
    """per-instance 敏感 env 的 Secret 名(JUPYTER_TOKEN 等)。

    与实例同生命周期(delete_instance 一并删除);Pod spec 只以 secretKeyRef 引用,
    明文不落 spec(不进 etcd 明文面/审计快照,只读 SA 的 pods:get 也读不到)。
    """
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
    # LB/NodePort 端口池分配;None = 该实例不开 SSH(服务型实例默认不占端口池)
    ssh_node_port: int | None
    jupyter_host: str  # <uuid>.app.<域名>,HTTPRoute hostname
    env: dict[str, str] = field(default_factory=dict)  # 非敏感环境变量
    # 敏感环境变量(如 JUPYTER_TOKEN):不落 Pod spec(明文 env 会进 etcd/审计日志/
    # 任何 pods:get 身份),由编排层写 per-instance Secret,Pod 以 secretKeyRef 引用
    secret_env: dict[str, str] = field(default_factory=dict)
    authorized_keys: tuple[str, ...] = ()
    node_selector: dict[str, str] = field(default_factory=dict)  # 池标签
    data_disk_subpath: str | None = None  # JuiceFS 子路径(挂 /root/data)
    scheduler_name: str | None = None  # 指定调度器(HAMi 池 = hami-scheduler)
    annotations: dict[str, str] = field(default_factory=dict)  # 如 HAMi use-gputype
    # 平台托管的镜像拉取凭据 Secret 名(core/registry.PULL_SECRET_NAME);
    # None = 项目 public / 未配机器人
    image_pull_secret: str | None = None

    # ---- 服务型实例(workload_type='service')。dev 形态全取默认值,行为逐字不变 ----
    # Never = 容器退出即 Pod 终态(dev:Jupyter 挂了就该判故障);
    # Always = kubelet 原地重启容器、Pod 不重建 —— 服务要的就是这个,而且它保住了
    # reconciler 的「Pod 名恒等于实例 uuid」假设(重建 Pod 会换名字,那套全塌)
    restart_policy: str = "Never"
    command: tuple[str, ...] | None = None  # 覆盖镜像 ENTRYPOINT;None = 用镜像自带
    args: tuple[str, ...] | None = None
    service_port: int | None = None  # 非空 → 建 <name>-svc ClusterIP + 服务 HTTPRoute
    service_host: str | None = None  # <slug>.svc.<域名>,服务 HTTPRoute 的 hostname
    # 非空 → 挂 readinessProbe + startupProbe(httpGet)。
    # startupProbe 不是可选项:只有 readiness 时,加载大模型权重的容器在
    # 启动阶段就被判 not-ready,而 not-ready 会触发 reconciler 的可用性判定。
    health_path: str | None = None
    # False → 不建 SSH NodePort Service(服务型实例默认如此,不占端口池)
    with_ssh: bool = True


class NodePortTaken(Exception):
    """请求的 NodePort 已被集群里的其它对象占用(apiserver 422)。

    调用方应把该端口标 blocked 并换一个重试。
    """

    def __init__(self, port: int) -> None:
        super().__init__(f"node port {port} already allocated")
        self.port = port


@dataclass(frozen=True)
class PodStatus:
    """Pod 状态:get_status 单查与 list_instance_pods 全量 LIST 同一形状。

    LIST 条目还带归属(namespace/name)与 labels:reconciler 以全量 LIST 替代逐实例
    get_status(读放大控制);泄漏回收据 labels 豁免受管 Job(wipe/quota)的子孙 Pod——
    它们带 MANAGED_LABEL 会被 LIST 命中,但名字不是实例 uuid,无 labels 无法与真泄漏区分。
    """

    exists: bool
    ready: bool = False
    phase: str = "Unknown"  # Pending / Running / Succeeded / Failed / Unknown
    node_name: str | None = None
    # deletionTimestamp 已设 = Terminating:read 仍 200、phase 仍 Running,判活必须看这个字段
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
    # Gateway 对象 status.conditions 的 Programmed=True(租户 Jupyter 入口)。
    # 只探控制器 Deployment 不够:CRD 装了、控制器活着,但 listener 的证书 Secret 缺失
    # 或 hostname 冲突时,Programmed 仍为 False 而流量一条都进不来。
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
        """在 namespace 写/覆写平台托管的镜像拉取 Secret(kubernetes.io/dockerconfigjson,
        名字 core/registry.PULL_SECRET_NAME)。annotation 指纹相同即跳过;轮换只改配置中心,
        下一次建 Pod / 预热前自动覆写,节点不落凭据。幂等。"""
        ...

    async def create_instance(self, spec: InstancePodSpec) -> None:
        """创建 Pod + Service + HTTPRoute。已存在则跳过。

        建哪些对象随形态走:dev 建 SSH NodePort + Jupyter ClusterIP + Jupyter HTTPRoute;
        service 按 with_ssh 决定要不要 SSH,建 <name>-svc ClusterIP + 服务 HTTPRoute,
        不建 Jupyter 的任何对象。
        """
        ...

    async def delete_instance(self, namespace: str, name: str, *, force: bool = False) -> None:
        """删除该实例的 Pod/Service/HTTPRoute(两种形态的对象一律尝试删,不存在即跳过 ——
        删除路径不该依赖「这台当初是什么形态」的记忆)。**不动实例盘**,盘必须活过关机
        (见 delete_instance_disk)。不存在则跳过。

        force=True 走强制删除(gracePeriodSeconds=0,不等 kubelet 确认),只在节点已失联时用
        —— 节点失联时优雅删除永远完不成。
        """
        ...

    async def delete_instance_disk(self, namespace: str, name: str) -> None:
        """删除该实例的实例盘 PVC。只允许在实例真正终结时调用(释放/回收,以及从未跑起来过的
        creating 超时);关机、重启、pod_lost 都不许调。不存在则跳过;调用点须先确认 Pod 已消失,
        否则 pvc-protection 会让删除挂起。"""
        ...

    async def get_status(self, namespace: str, name: str) -> PodStatus: ...

    async def read_instance_logs(
        self, namespace: str, name: str, *, tail_lines: int, since_seconds: int | None = None
    ) -> str:
        """读取实例容器日志(只读):末尾 tail_lines 行,可选 since_seconds 时间窗。

        请求路径同步直读的例外(实时性,不进 outbox);调用方须自行做 owner/状态/限流校验。
        """
        ...

    async def list_instance_pods(self) -> list[PodStatus]:
        """全量列出租户实例 Pod 状态(reconciler 每轮一次,替代逐实例 get_status)。"""
        ...

    async def list_instance_endpoints(self) -> list[tuple[str, str]]:
        """列出全部租户实例的 Service/HTTPRoute (namespace, 实例名;jupyter 副名已归并)。
        reconciler 孤儿端点清理用(残留端点会持续占 NodePort)。"""
        ...

    async def used_node_ports(self) -> set[int]:
        """集群内受管 Service 当前占用的 NodePort 集合。blocked 端口复检用。"""
        ...

    async def wipe_disk(self, namespace: str, subpath: str) -> None:
        """真实擦除数据盘的 JuiceFS 子路径(集群侧 Job)。幂等;
        未完成时抛异常交 outbox 退避重试,下次执行看到已完成即返回。"""
        ...

    async def set_disk_quota(self, subpath: str, capacity_gb: int) -> None:
        """下发 JuiceFS 目录硬配额(平台 ns 的 CLI Job,纯元数据操作)。幂等;
        Job 进行中/失败抛异常交 outbox 退避重试。配额是纯元数据,不挂卷。"""
        ...

    async def delete_disk_quota(self, subpath: str) -> None:
        """删盘前摘除目录配额(无配额记录视为成功)。幂等;失败抛异常。"""
        ...

    async def list_nodes(self, include_unlabeled: bool = False) -> list["NodeInfo"]:
        """节点视图。include_unlabeled=True 时包含未打池标签的节点(台账巡检用);
        默认仅带 superdl.io/pool 标签的节点。"""
        ...

    async def set_node_labels(self, node_name: str, labels: dict[str, str]) -> None:
        """merge-patch 节点 labels(巡检收敛 superdl.io/gpu-model 用)。幂等。"""
        ...

    async def prewarm_image(
        self, node_name: str, image_ref: str, *, image_pull_secret: str | None = None
    ) -> None:
        """在指定节点创建镜像预热 Job(nodeName 定点拉取,image_pull_secret 为平台托管的
        拉取凭据 Secret 名)。创建后即返回不等待,完成态由巡检经 get_prewarm_status 收敛;
        已存在同名 Job 则跳过(幂等)。"""
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


GPU_MODEL_NODE_LABEL = (
    "superdl.io/gpu-model"  # 平台 canonical 型号标签(巡检写入,调度 nodeSelector 依赖)
)
POOL_NODE_LABEL = "superdl.io/pool"  # 节点池标签(装机时定死;kata / hami / mig 分池铁律)
# 平台受管对象标签:实例 Pod/Service/HTTPRoute/受管 Job 均打此标,全量 LIST 的过滤依据。
# 租户 namespace 也打这一个标签,同时兼作 Gateway `app-https` listener 的
# allowedRoutes.namespaces.from=Selector 选择器 —— 平台自身 ns 不带此标,
# 于是该 selector 精确等于「全部租户 ns 且仅租户 ns」,不必再造一个标签。
MANAGED_LABEL = "superdl.io/managed"
# K8s 控制器自动打在 Job 子孙 Pod 上的标签:泄漏回收的豁免依据
# (Job 泄漏由 ttl_seconds_after_finished 兜底,不属"未知 Pod 强删"范围)
JOB_NAME_LABEL = "batch.kubernetes.io/job-name"


@dataclass(frozen=True)
class NodeInfo:
    """管理端节点视图。"""

    name: str
    pool_label: str
    gpu_total: int
    gpu_used: int
    status: str  # Ready / NotReady / Cordoned
    # 节点物理规格(取自 K8s node.status.capacity;0 表示未知/未上报)
    vcpu: int = 0
    mem_gb: int = 0
    disk_gb: int = 0
    # 台账巡检用:GFD 型号原文标签(nvidia.com/gpu.product)与平台 canonical 标签当前值(空串=无)
    gpu_model_label: str = ""
    model_label_current: str = ""
    # GFD 驱动/CUDA 版本标签(nvidia.com/cuda.{driver,runtime}-version.full;
    # 空串=无 GFD 或非 GPU 节点)
    driver_version_label: str = ""
    cuda_version_label: str = ""
