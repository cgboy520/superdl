from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# cpu = 无卡节点池;装机不打 NVIDIA operand 标签、跳过 GPU 探测
Pool = Literal["kata", "hami", "mig", "cpu"]

HOSTNAME_PATTERN = r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$"


class EnrollmentCreate(BaseModel):
    pool: Pool
    # 签发时绑定期望主机名;上报不符即 failed
    hostname: str = Field(min_length=1, max_length=253, pattern=HOSTNAME_PATTERN)
    note: str | None = Field(default=None, max_length=128)
    nvme_devices: list[str] | None = Field(default=None, max_length=16)
    ttl_hours: int = Field(default=24, ge=1, le=168)


class NodeEnrollmentOut(BaseModel):
    """列表/进度视图,不含 token。"""

    id: int
    pool: str
    hostname: str | None
    note: str | None
    status: str  # pending/installing/rebooting/joining/joined/failed/expired/revoked
    phase: str | None
    error: str | None
    node_name: str | None
    reported_ip: str | None
    expires_at: datetime
    last_report_at: datetime | None
    joined_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class EnrollmentCommandOut(BaseModel):
    """创建/重生成响应:token 明文仅此一次出现。"""

    enrollment: NodeEnrollmentOut
    token: str
    curl_command: str
    wget_command: str


class NodeDecommissionRequest(BaseModel):
    # 原因必填,落审计 detail
    reason: str = Field(min_length=2, max_length=256)


class NodeDecommissionOut(BaseModel):
    """退役受理回执:停调度期望态 + 令牌作废已生效;删 Node 对象经 outbox 异步。"""

    node_name: str
    revoked_enrollments: int  # 本次被置 revoked 的注册登记行数
    queued: bool = True


# ---------- 匿名侧(节点上的脚本调用) ----------


class BootstrapRequest(BaseModel):
    hostname: str = Field(min_length=1, max_length=253, pattern=HOSTNAME_PATTERN)
    os_info: dict[str, Any] = Field(default_factory=dict)  # {os_release, kernel, arch}
    # 全卡清单 [{name, memory_mib?}];无驱动时 lspci 名称回落(无 memory_mib)
    gpu_details: list[dict[str, Any]] = Field(default_factory=list, max_length=16)


class BootstrapOut(BaseModel):
    """装机参数下发,含 join token 明文,不入日志。"""

    pool: str
    k8s_distro: str  # rke2 | k3s,平台探测派生
    cluster_agent_version: str
    cluster_server_url: str
    cluster_join_token: str
    driver_version: str
    nvme_devices: list[str]
    registries_yaml: str  # 节点 registries.yaml 正文(Spegel / Harbor 代理缓存 / CA;不含凭据)
    registry_ca_pem: str = ""  # Harbor 自签/私有 CA(PEM);非空时脚本落 $RANCHER_DIR/harbor-ca.crt
    install_mirror: str = "cn"  # 装机安装源:cn=rancher 国内镜像 / official
    # bootstrap 换发的窄权限令牌(仅 /progress),此后上报只用它
    progress_token: str
    script_sha256: str  # 当前下发脚本的指纹:重启续跑重拉自身时校验,防中途替换


class ProgressRequest(BaseModel):
    phase: str = Field(min_length=1, max_length=32)
    state: Literal["running", "ok", "failed", "rebooting"]
    message: str | None = Field(default=None, max_length=2000)
    # 驱动/CUDA 版本:收尾上报(waiting_node)附带,写进登记快照 os_info
    driver_version: str | None = Field(default=None, max_length=32)
    cuda_version: str | None = Field(default=None, max_length=16)
