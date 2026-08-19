"""K8s 编排抽象:orchestrator 只面向本协议编程,dev/test 用 Fake,生产用 Real。

任何直接 import kubernetes 客户端的业务代码都是违规 —— 必须经此层。
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


@dataclass(frozen=True)
class PodStatus:
    exists: bool
    ready: bool = False
    phase: str = "Unknown"  # Pending / Running / Succeeded / Failed / Unknown
    node_name: str | None = None


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

    async def available_gpus(self, pool_label: str) -> int:
        """池内近似可租卡数(近似库存;创建以调度结果为准)。"""
        ...

    async def list_nodes(self) -> list["NodeInfo"]:
        """管理端节点视图。"""
        ...


@dataclass(frozen=True)
class NodeInfo:
    """管理端节点视图。"""

    name: str
    pool_label: str
    gpu_model: str
    gpu_total: int
    gpu_used: int
    status: str  # Ready / NotReady / Cordoned
