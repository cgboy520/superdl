"""K8s 编排抽象:orchestrator 只面向本协议编程,dev/test 用 Fake,生产用 Real。

业务代码禁止直接 import kubernetes 客户端。
"""

from dataclasses import dataclass, field
from typing import Protocol

# 存储契约:这三个名字必须与 deploy/cluster/values/{topolvm,juicefs}.yaml 里真实创建的
# StorageClass 对得上。K8s 创建 PVC 时不校验 SC 是否存在,名字错了不会报错 ——
# PVC 永久 Pending,实例卡到 300 秒超时才 failed,用户只看到「开不出来」。
# 下发门禁(nodes.require_storage_classes)按名核对已探测到的 SC,把这类错配变成即时 409。
INSTANCE_DISK_STORAGE_CLASS = "topolvm-provisioner"  # 实例盘:节点本地 NVMe LV
JUICEFS_STORAGE_CLASS = "superdl-juicefs"  # 数据盘:JuiceFS 共享后端
JUICEFS_PVC_NAME = "juicefs-shared"  # 每租户 ns 一只共享 PVC(数据盘按 subPath 切分)


def instance_disk_pvc_name(instance_name: str) -> str:
    """实例盘 PVC 名。平台自管生命周期(只在释放/回收时删),不是 Pod 拥有的
    ephemeral volume —— 后者会让「关机」把用户数据一起抹掉。"""
    return f"{instance_name}-root"


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
    ssh_node_port: int  # LB/NodePort 端口池分配
    jupyter_host: str  # <uuid>.app.<域名>,Ingress host 路由
    env: dict[str, str] = field(default_factory=dict)  # 含 JUPYTER_TOKEN
    authorized_keys: tuple[str, ...] = ()
    node_selector: dict[str, str] = field(default_factory=dict)  # 池标签
    data_disk_subpath: str | None = None  # JuiceFS 子路径(挂 /root/data)
    scheduler_name: str | None = None  # 指定调度器(HAMi 池 = hami-scheduler)
    annotations: dict[str, str] = field(default_factory=dict)  # 如 HAMi use-gputype


@dataclass(frozen=True)
class PodStatus:
    exists: bool
    ready: bool = False
    phase: str = "Unknown"  # Pending / Running / Succeeded / Failed / Unknown
    node_name: str | None = None
    # deletionTimestamp 已设 = 正在优雅删除(Terminating)。对象仍在 etcd 里、
    # read 仍 200、phase 仍是 Running —— 只看 exists/phase 的代码会把它当活着的 Pod。
    deleting: bool = False


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
    storage_classes: tuple[str, ...] = ()
    runtime_classes: tuple[str, ...] = ()
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

    async def create_instance(self, spec: InstancePodSpec) -> None:
        """创建 Pod + Service(SSH NodePort)+ Ingress(Jupyter)。已存在则跳过。"""
        ...

    async def delete_instance(self, namespace: str, name: str) -> None:
        """删除该实例的 Pod/Service/Ingress。**不动实例盘** —— 关机就是删 Pod,
        盘必须活过关机(见 delete_instance_disk)。不存在则跳过。"""
        ...

    async def delete_instance_disk(self, namespace: str, name: str) -> None:
        """删除该实例的实例盘 PVC。只允许在实例真正终结时调用(释放/回收,
        以及从未跑起来过的 creating 超时);关机、重启、pod_lost 都不许调。
        不存在则跳过;Pod 还在时 K8s 的 pvc-protection 会让删除挂起,
        故调用点必须先确认 Pod 已消失。"""
        ...

    async def get_status(self, namespace: str, name: str) -> PodStatus: ...

    async def list_instance_pods(self) -> list[tuple[str, str]]:
        """列出全部租户实例 Pod (namespace, name)。reconciler 泄漏检测用。"""
        ...

    async def wipe_disk(self, namespace: str, subpath: str) -> None:
        """真实擦除数据盘的 JuiceFS 子路径(集群侧 Job)。幂等;
        未完成时抛异常交 outbox 退避重试,下次执行看到已完成即返回。"""
        ...

    async def available_gpus(self, pool_label: str) -> int:
        """池内近似可租卡数(近似库存;创建以调度结果为准)。"""
        ...

    async def list_nodes(self, include_unlabeled: bool = False) -> list["NodeInfo"]:
        """节点视图。include_unlabeled=True 时包含未打池标签的节点(台账巡检用);
        默认仅带 superdl.io/pool 标签的节点。"""
        ...

    async def set_node_labels(self, node_name: str, labels: dict[str, str]) -> None:
        """merge-patch 节点 labels(巡检收敛 superdl.io/gpu-model 用)。幂等。"""
        ...

    async def prewarm_image(self, node_name: str, image_ref: str) -> None:
        """在指定节点创建镜像预热 Job(nodeName 定点拉取)。创建后即返回不等待,
        完成态由巡检经 get_prewarm_status 收敛;已存在同名 Job 则跳过(幂等)。"""
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


@dataclass(frozen=True)
class NodeInfo:
    """管理端节点视图。"""

    name: str
    pool_label: str
    gpu_model: str
    gpu_total: int
    gpu_used: int
    status: str  # Ready / NotReady / Cordoned
    # 节点物理规格(取自 K8s node.status.capacity;0 表示未知/未上报)
    vcpu: int = 0
    mem_gb: int = 0
    disk_gb: int = 0
    # 台账巡检用:GFD 原文标签与平台 canonical 标签当前值(空串=无)
    gpu_model_label: str = ""
    model_label_current: str = ""
