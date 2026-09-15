"""K8s 编排抽象:orchestrator 只面向本协议编程,dev/test 用 Fake,生产用 Real;业务代码禁止直接
import kubernetes 客户端。"""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

INSTANCE_DISK_STORAGE_CLASS = "topolvm-provisioner"
DATA_DISK_STORAGE_CLASS = "superdl-cephfs"

GATEWAY_NAMESPACE = "superdl"
GATEWAY_NAME = "superdl"
GATEWAY_APP_LISTENER = "app-https"
GATEWAY_SVC_LISTENER = "svc-https"
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
    """返回平台管理的实例盘 PVC 名。"""
    return f"{instance_name}-root"


def data_disk_pvc_name(disk_uuid: str) -> str:
    """返回数据盘 PVC 名;调用方须提供合法的 disk_uuid。"""
    return f"disk-{disk_uuid}"


def instance_env_secret_name(instance_name: str) -> str:
    """per-instance 敏感 env 的 Secret 名(JUPYTER_TOKEN 等),随实例删除。"""
    return f"jupyter-{instance_name}"


@dataclass(frozen=True)
class InstancePodSpec:
    """租户实例的 K8s 参数;敏感环境变量必须放入 secret_env,不得放入 env。"""

    namespace: str
    name: str
    image: str
    gpu_resources: dict[str, str]
    runtime_class: str | None
    host_users: bool
    vcpu: int
    mem_gb: int
    disk_gb: int
    ssh_node_port: int | None
    jupyter_host: str
    env: dict[str, str] = field(default_factory=dict)
    secret_env: dict[str, str] = field(default_factory=dict)
    authorized_keys: tuple[str, ...] = ()
    node_selector: dict[str, str] = field(default_factory=dict)
    data_disk_pvc: str | None = None
    scheduler_name: str | None = None
    annotations: dict[str, str] = field(default_factory=dict)
    image_pull_secret: str | None = None

    restart_policy: str = "Never"
    command: tuple[str, ...] | None = None
    args: tuple[str, ...] | None = None
    service_port: int | None = None
    service_host: str | None = None
    health_path: str | None = None
    with_ssh: bool = True


class NodePortTaken(Exception):
    """请求的 NodePort 已被其它对象占用(apiserver 422);调用方标 blocked 并换端口。"""

    def __init__(self, port: int) -> None:
        super().__init__(f"node port {port} already allocated")
        self.port = port


@dataclass(frozen=True)
class PodStatus:
    """Pod 状态;deleting 表示已请求删除但对象仍存在,独立于 phase。"""

    exists: bool
    ready: bool = False
    phase: str = "Unknown"
    node_name: str | None = None
    deleting: bool = False
    namespace: str = ""
    name: str = ""
    labels: dict[str, str] = field(default_factory=dict)


ComponentState = Literal["ok", "degraded", "down", "disabled", "unknown"]
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
    """将组件事实转换为可写入 JSONB 的字典和列表。"""
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
    """解析组件事实;跳过非字典或 state 非字符串的项,过滤无效明细并归一化字段。"""
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
    """体检项实时明细:事实、Pod 或证书对象、告警事件。"""

    facts: tuple[ComponentFact, ...] = ()
    pods: tuple[ComponentObject, ...] = ()
    events: tuple[ComponentObject, ...] = ()


@dataclass(frozen=True)
class ClusterProbe:
    """集群能力探测快照(nodes 巡检落 cluster_status 表,门禁与集群页读表不实时探测)。"""

    api_reachable: bool
    k8s_version: str | None = None
    distro: str | None = None
    hami_ready: bool = False
    dcgm_present: bool = False
    kps_present: bool = False
    gpu_operator_present: bool = False
    kata_runtimeclass: bool = False
    nvidia_runtimeclass: bool = False
    gateway_ready: bool = False
    cert_manager_ready: bool = False
    nodes_ready: int = 0
    nodes_total: int = 0
    storage_classes: tuple[str, ...] = ()
    pools: dict[str, int] = field(default_factory=dict)
    pools_ready: dict[str, int] = field(default_factory=dict)
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

    state: str
    message: str | None = None


class K8sOrchestrator(Protocol):
    """全部操作必须幂等(outbox at-least-once 语义)。"""

    async def ensure_namespace(self, namespace: str) -> None:
        """创建或更新租户 namespace 标签、RBAC、NetworkPolicy、ResourceQuota 和 LimitRange。"""
        ...

    async def ensure_pull_secret(
        self, namespace: str, dockerconfigjson: str, fingerprint: str
    ) -> None:
        """在 namespace 写/覆写镜像拉取 Secret(dockerconfigjson,core/registry.PULL_SECRET_NAME);
        annotation 指纹相同即跳过。幂等。"""
        ...

    async def create_instance(self, spec: InstancePodSpec) -> None:
        """创建实例盘、环境变量 Secret、Pod、Service 和 HTTPRoute,支持重放。

        Secret 和 SSH 端口可收敛;删除中的 Pod/SSH Service 须等待消失后重试。
        """
        ...

    async def delete_instance(self, namespace: str, name: str, *, force: bool = False) -> None:
        """删除实例 Pod、Service、HTTPRoute 和环境变量 Secret;忽略不存在的对象,保留实例盘。
        force=True 使用零宽限期删除,只在节点失联时用。
        """
        ...

    async def delete_instance_disk(self, namespace: str, name: str) -> None:
        """删除实例盘 PVC。只在实例终结(释放/回收/creating 超时)时调用,关机、重启、pod_lost 不许调;
        调用点须先确认 Pod 已消失。不存在则跳过。"""
        ...

    async def get_status(self, namespace: str, name: str) -> PodStatus: ...

    async def read_instance_logs(self, namespace: str, name: str, *, tail_lines: int) -> str:
        """读取实例容器日志末尾 tail_lines 行;允许请求路径直读,调用方必须鉴权和限流。"""
        ...

    async def list_instance_pods(self) -> list[PodStatus]:
        """列出租户命名空间内受管 Pod 的状态,包含受管 Job 的 Pod。"""
        ...

    async def list_instance_endpoints(self) -> list[tuple[str, str]]:
        """列出全部租户实例的 Service/HTTPRoute (namespace, 实例名),副名已归并;孤儿端点清理用。"""
        ...

    async def used_node_ports(self) -> set[int]:
        """返回集群全部 Service 占用的 NodePort,包含非平台对象。"""
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


GPU_MODEL_NODE_LABEL = "superdl.io/gpu-model"
POOL_NODE_LABEL = "superdl.io/pool"
GPU_WORKLOAD_CONFIG_LABEL = "nvidia.com/gpu.workload.config"
GPU_DEPLOY_DEVICE_PLUGIN_LABEL = "nvidia.com/gpu.deploy.device-plugin"
MANAGED_LABEL = "superdl.io/managed"
JOB_NAME_LABEL = "batch.kubernetes.io/job-name"


@dataclass(frozen=True)
class NodeInfo:
    """管理端节点视图。"""

    name: str
    pool_label: str
    gpu_total: int
    gpu_used: int
    status: str
    vcpu: int = 0
    mem_gb: int = 0
    disk_gb: int = 0
    gpu_model_label: str = ""
    model_label_current: str = ""
    driver_version_label: str = ""
    cuda_version_label: str = ""
