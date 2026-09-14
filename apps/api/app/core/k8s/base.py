"""K8s 编排抽象:orchestrator 只面向本协议编程,dev/test 用 Fake,生产用 Real;业务代码禁止直接
import kubernetes 客户端。"""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

# StorageClass 名,与 deploy/cluster/values/{topolvm,rook-ceph-cluster}.yaml 一致;下发门禁按名核对
INSTANCE_DISK_STORAGE_CLASS = "topolvm-provisioner"  # 实例盘:节点本地 NVMe LV
# 数据盘:CephFS。一盘一 PVC,PVC 容量即硬配额(CSI 建带配额的 subvolume),不另下发。
# 选 CephFS 而非 FUSE 类后端:内核 cephfs 声明 FS_ALLOW_IDMAP,能挂进 hostUsers: false 的租户 Pod
DATA_DISK_STORAGE_CLASS = "superdl-cephfs"

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


def data_disk_pvc_name(disk_uuid: str) -> str:
    """数据盘 PVC 名(租户 ns 内唯一);uuid 是 32 位 hex,拼出来天然是合法 DNS 名。"""
    return f"disk-{disk_uuid}"


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
    data_disk_pvc: str | None = None  # 数据盘 PVC 名(挂 /root/data);None = 未挂盘
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


# 体检项五态。ok/degraded/down/disabled 由探测侧按事实判;unknown 只由渲染侧在快照过期时覆写。
ComponentState = Literal["ok", "degraded", "down", "disabled", "unknown"]
# 事实的着色意图;由探测侧标注,前端按语义色渲染,不在文案里写形容词
FactTone = Literal["normal", "warn", "bad"]


@dataclass(frozen=True)
class ComponentFact:
    """一条可核对的事实。key 是文案后缀(前端出 label),value 是纯数据:计数、版本、对象名、
    地址、时长。value 不含语言,不随 locale 变。"""

    key: str
    value: str
    tone: FactTone = "normal"


@dataclass(frozen=True)
class ComponentObject:
    """抽屉对象表的一行。name 是对象名;fields 的键是列名后缀,值同样是纯数据。"""

    name: str
    fields: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ComponentFacts:
    """单个体检项的探测结果。headline 是面板主数字,facts 是面板与抽屉的事实行,
    objects 是抽屉里的对象级明细(DaemonSet / listener / StorageClass / 节点)。"""

    state: ComponentState
    headline: ComponentFact | None = None
    facts: tuple[ComponentFact, ...] = ()
    objects: tuple[ComponentObject, ...] = ()


def _fact_to_json(f: ComponentFact) -> dict[str, str]:
    return {"key": f.key, "value": f.value, "tone": f.tone}


def component_facts_to_json(facts: dict[str, ComponentFacts]) -> dict[str, Any]:
    """落 JSONB。dataclass → 原始 dict,不用 asdict:tuple 要显式转 list 才好序列化。"""
    return {
        key: {
            "state": cf.state,
            "headline": _fact_to_json(cf.headline) if cf.headline else None,
            "facts": [_fact_to_json(f) for f in cf.facts],
            "objects": [{"name": o.name, "fields": dict(o.fields)} for o in cf.objects],
        }
        for key, cf in facts.items()
    }


def component_facts_from_json(raw: Any) -> dict[str, ComponentFacts]:
    """读 JSONB。库里可能是上一版写的行,结构对不上就整项丢弃(渲染侧按缺项处理)。"""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, ComponentFacts] = {}
    for key, item in raw.items():
        if not isinstance(item, dict) or not isinstance(item.get("state"), str):
            continue
        head = item.get("headline")
        out[str(key)] = ComponentFacts(
            state=item["state"],
            headline=_fact_from_json(head),
            facts=tuple(
                f
                for f in (_fact_from_json(x) for x in _as_list(item.get("facts")))
                if f is not None
            ),
            objects=tuple(
                ComponentObject(name=str(o.get("name", "")), fields=_as_str_map(o.get("fields")))
                for o in _as_list(item.get("objects"))
                if isinstance(o, dict)
            ),
        )
    return out


def _as_list(v: Any) -> list[Any]:
    return v if isinstance(v, list) else []


def _as_str_map(v: Any) -> dict[str, str]:
    if not isinstance(v, dict):
        return {}
    return {str(k): str(val) for k, val in v.items()}


def _fact_from_json(v: Any) -> ComponentFact | None:
    if not isinstance(v, dict) or not isinstance(v.get("key"), str):
        return None
    tone = v.get("tone")
    return ComponentFact(
        key=v["key"],
        value=str(v.get("value", "")),
        tone=tone if tone in ("normal", "warn", "bad") else "normal",
    )


@dataclass(frozen=True)
class ComponentDetail:
    """体检项的实时深探结果:快照之外的现场明细。

    快照是 60s 一轮的巡检产物,回答「就绪几个」;深探回答「为什么不就绪」——
    Pod 级失败原因、最近告警事件、证书到期日,这些变化快、体量大,不适合常驻落库。
    """

    facts: tuple[ComponentFact, ...] = ()
    pods: tuple[ComponentObject, ...] = ()
    events: tuple[ComponentObject, ...] = ()


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
    # 池→Ready 且可调度的节点数。档位可用性看这个,不看 pools:池里三台全 NotReady 一样开不了机
    pools_ready: dict[str, int] = field(default_factory=dict)
    # 体检项 key → 探测事实。布尔列只够门禁用,面板与抽屉读这里
    component_facts: dict[str, ComponentFacts] = field(default_factory=dict)
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

    async def ensure_data_disk(self, namespace: str, name: str, size_gb: int) -> None:
        """建或扩数据盘 PVC(幂等):不存在则建,已存在且更小则扩容。
        PVC 容量即硬配额,不另下发。扩容后端不支持时抛异常交 outbox 重试。"""
        ...

    async def delete_data_disk(self, namespace: str, name: str) -> None:
        """删除数据盘 PVC(reclaimPolicy=Delete,CSI 随之销毁 subvolume)。
        不存在或 ns 已消失视为成功。"""
        ...

    async def list_nodes(self, include_unlabeled: bool = False) -> list["NodeInfo"]:
        """节点视图;默认仅带 superdl.io/pool 标签的节点,include_unlabeled=True 含未打标节点。"""
        ...

    async def set_node_labels(self, node_name: str, labels: dict[str, str | None]) -> None:
        """merge-patch 节点 labels(巡检收敛 superdl.io/gpu-model、切池收敛池与 operand 标签)。
        值为 None = 删该键(merge-patch 原生语义)。幂等。"""
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

    async def probe_component_detail(self, key: str) -> "ComponentDetail":
        """单个体检项的实时深探(只读):Pod 级失败原因、最近告警事件、证书到期。
        未知 key 或该项无可深探的对象时返回空结果,不抛。"""
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
POOL_NODE_LABEL = "superdl.io/pool"  # 节点池标签(装机时定,空节点可切;kata / hami / mig 分池铁律)
# GPU Operator 的 operand 落点标签:切池时随池标签一起收敛(准入策略③ 白名单里的两个具名键)
GPU_WORKLOAD_CONFIG_LABEL = "nvidia.com/gpu.workload.config"
GPU_DEPLOY_DEVICE_PLUGIN_LABEL = "nvidia.com/gpu.deploy.device-plugin"
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
