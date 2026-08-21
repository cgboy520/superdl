"""K8s 编排抽象:orchestrator 只面向本协议编程,dev/test 用 Fake,生产用 Real。

业务代码禁止直接 import kubernetes 客户端。
"""

from dataclasses import dataclass, field
from typing import Protocol


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


@dataclass(frozen=True)
class PodStatus:
    exists: bool
    ready: bool = False
    phase: str = "Unknown"  # Pending / Running / Succeeded / Failed / Unknown
    node_name: str | None = None


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
        """删除该实例的全部对象。不存在则跳过。"""
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
        默认仅带 superdl.io/pool 标签的节点(既有调用方语义不变)。"""
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
