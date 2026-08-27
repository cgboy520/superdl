from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# cpu = 无卡节点池(纯 CPU 实例);装机时不打任何 NVIDIA operand 标签、跳过 GPU 探测
Pool = Literal["kata", "hami", "mig", "cpu"]

HOSTNAME_PATTERN = r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$"


class EnrollmentCreate(BaseModel):
    pool: Pool
    # 签发时强制绑定期望主机名:令牌与机器一一对应,被盗令牌无法在其他机器上
    # bootstrap 换出 join token(service 层对上报不符者直接 failed,防令牌串用)
    hostname: str = Field(min_length=1, max_length=253, pattern=HOSTNAME_PATTERN)
    note: str | None = Field(default=None, max_length=128)
    nvme_devices: list[str] | None = Field(default=None, max_length=16)
    ttl_hours: int = Field(default=24, ge=1, le=168)


class NodeEnrollmentOut(BaseModel):
    """列表/进度视图 —— 永不含 token。"""

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


# ---------- 匿名侧(节点上的脚本调用) ----------


class BootstrapRequest(BaseModel):
    hostname: str = Field(min_length=1, max_length=253, pattern=HOSTNAME_PATTERN)
    os_info: dict[str, Any] = Field(default_factory=dict)  # {os_release, kernel, arch}
    # 全卡清单 [{name, memory_mib?}]:nvidia-smi 可用时带显存(台账显存口径);
    # 无驱动时脚本以 lspci 名称回落(无 memory_mib,巡检按型号默认表补显存)
    gpu_details: list[dict[str, Any]] = Field(default_factory=list, max_length=16)


class BootstrapOut(BaseModel):
    """装机参数下发 —— 含 join token 明文,仅经 Bearer POST 响应体,严禁入日志。"""

    pool: str
    k8s_distro: str  # rke2 | k3s,平台探测派生
    rke2_version: str
    rke2_server_url: str
    rke2_join_token: str
    driver_version: str
    nvme_devices: list[str]
    registries_yaml: str  # 节点 registries.yaml 正文(Spegel / Harbor 代理缓存 / CA;不含凭据)
    registry_ca_pem: str = ""  # Harbor 自签/私有 CA(PEM);非空时脚本落 $RANCHER_DIR/harbor-ca.crt
    install_mirror: str = "cn"  # 装机安装源:cn=rancher 国内镜像 / official
    # 首次 bootstrap 换发的窄权限令牌(仅 /progress 上报),此后上报与断点续跑只用它
    progress_token: str
    script_sha256: str  # 当前下发脚本的指纹:重启续跑重拉自身时校验,防中途替换


class ProgressRequest(BaseModel):
    phase: str = Field(min_length=1, max_length=32)
    state: Literal["running", "ok", "failed", "rebooting"]
    message: str | None = Field(default=None, max_length=2000)
    # 驱动/CUDA 版本:脚本在驱动已加载的收尾上报(waiting_node)附带——首装要经一次重启,
    # bootstrap 时采不到;服务端写进登记快照 os_info,巡检据此填台账 driver/cuda 列
    driver_version: str | None = Field(default=None, max_length=32)
    cuda_version: str | None = Field(default=None, max_length=16)
