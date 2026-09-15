from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.gpu_adapter import TIER_CPU, TIERS
from app.core.messages import render_message
from app.core.money import MoneyOut
from app.core.registry import is_valid_image_ref

_TIER_PATTERN = f"^({'|'.join(TIERS)})$"


class PaymentChannelsOut(BaseModel):
    """可用支付渠道(充值弹窗按此启用 Tab)。"""

    wechat: bool
    alipay: bool
    mock: bool


class SiteConfigOut(BaseModel):
    """站点公开配置(页脚备案号与经营主体信息等,未登录可访问)。"""

    icp_number: str | None
    police_record_number: str | None
    support_email: str | None
    support_wechat: str | None
    company_name: str | None = None
    company_address: str | None = None
    company_phone: str | None = None
    business_license_url: str | None = None
    payment_channels: PaymentChannelsOut


class SkuMarketOut(BaseModel):
    """市场卡片视图(用户端)。"""

    id: int
    name: str
    gpu_model: str
    tier: str
    mig_profile: str | None
    gpu_cores_pct: int
    vram_gb: int
    vcpu: int
    mem_gb: int
    disk_gb: int
    price_hourly: MoneyOut
    max_gpus_per_instance: int
    cuda_max: str | None
    pool_label: str
    period_enabled: bool
    spot_enabled: bool
    available_count: int = 0

    model_config = {"from_attributes": True}


class SkuAdminOut(BaseModel):
    """管理端全量视图(含超卖参数与池标签)。"""

    id: int
    name: str
    gpu_model: str
    tier: str
    mig_profile: str | None
    gpu_cores_pct: int
    vram_gb: int
    oversell_cores: MoneyOut
    pool_label: str
    vcpu: int
    mem_gb: int
    disk_gb: int
    price_hourly: MoneyOut
    max_gpus_per_instance: int
    cuda_max: str | None
    period_enabled: bool
    spot_enabled: bool
    status: str
    created_at: datetime
    capacity_gpus: int = 0
    sold_share: str | None = None
    actual_oversell: str | None = None

    model_config = {"from_attributes": True}


def cpu_spec_error(
    *,
    tier: str,
    gpu_model: str,
    gpu_cores_pct: int,
    vram_gb: int,
    max_gpus_per_instance: int,
    mig_profile: str | None,
) -> str | None:
    """校验 CPU/GPU 字段组合,返回错误文案键或 None。

    CPU 要求型号、算力、显存、最大卡数与切片为空或零;GPU 要求前四项非空非零。
    """
    if tier == TIER_CPU:
        if gpu_model or gpu_cores_pct or vram_gb or max_gpus_per_instance or mig_profile:
            return "catalog.cpuSkuGpuFieldsMustBeZero"
        return None
    if not gpu_model or not gpu_cores_pct or not vram_gb or not max_gpus_per_instance:
        return "catalog.gpuSkuNeedsGpuFields"
    return None


class SkuCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    gpu_model: str = Field(max_length=32)
    tier: str = Field(pattern=_TIER_PATTERN)
    mig_profile: str | None = None
    gpu_cores_pct: int = Field(default=100, ge=0, le=100)
    vram_gb: int = Field(ge=0)
    oversell_cores: Decimal = Field(default=Decimal("1.00"), ge=Decimal("1.00"), le=Decimal("9.99"))
    pool_label: str = Field(min_length=1, max_length=32)
    vcpu: int = Field(ge=1)
    mem_gb: int = Field(ge=1)
    disk_gb: int = Field(default=100, ge=10)
    price_hourly: Decimal = Field(gt=Decimal("0"))
    max_gpus_per_instance: int = Field(default=1, ge=0, le=8)
    cuda_max: str | None = None
    period_enabled: bool = True
    spot_enabled: bool = False

    @model_validator(mode="after")
    def _tier_matches_gpu_fields(self) -> "SkuCreate":
        key = cpu_spec_error(
            tier=self.tier,
            gpu_model=self.gpu_model,
            gpu_cores_pct=self.gpu_cores_pct,
            vram_gb=self.vram_gb,
            max_gpus_per_instance=self.max_gpus_per_instance,
            mig_profile=self.mig_profile,
        )
        if key is not None:
            raise ValueError(render_message(key, None))
        return self


class SkuUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    mig_profile: str | None = None
    gpu_cores_pct: int | None = Field(default=None, ge=0, le=100)
    vram_gb: int | None = Field(default=None, ge=0)
    oversell_cores: Decimal | None = Field(default=None, ge=Decimal("1.00"), le=Decimal("9.99"))
    pool_label: str | None = None
    vcpu: int | None = Field(default=None, ge=1)
    mem_gb: int | None = Field(default=None, ge=1)
    disk_gb: int | None = Field(default=None, ge=10)
    price_hourly: Decimal | None = Field(default=None, gt=Decimal("0"))
    max_gpus_per_instance: int | None = Field(default=None, ge=0, le=8)
    cuda_max: str | None = None
    period_enabled: bool | None = None
    spot_enabled: bool | None = None
    status: str | None = Field(default=None, pattern="^(on|off)$")
    reason: str = Field(min_length=2, max_length=200)


class ImageOut(BaseModel):
    id: int
    framework: str
    framework_version: str
    python_version: str
    cuda_version: str
    image_ref: str
    is_prewarmed: bool

    model_config = {"from_attributes": True}


def _check_image_ref(v: str) -> str:
    """镜像引用形态校验,管理端写入时即拒。"""
    v = v.strip()
    if not is_valid_image_ref(v):
        raise ValueError(
            "image_ref 形态不合法(期望 <host>[:port]/<path>[:tag][@sha256:<64位小写十六进制>])"
        )
    return v


class ImageCreate(BaseModel):
    framework: str = Field(min_length=1, max_length=32)
    framework_version: str = Field(min_length=1, max_length=32)
    python_version: str = Field(min_length=1, max_length=16)
    cuda_version: str = Field(min_length=1, max_length=16)
    image_ref: str = Field(min_length=3, max_length=256)
    prewarm_enabled: bool = True
    sort: int = Field(default=0, ge=0, le=9999)

    @field_validator("image_ref")
    @classmethod
    def _valid_ref(cls, v: str) -> str:
        return _check_image_ref(v)


class ImageUpdate(BaseModel):
    framework: str | None = Field(default=None, min_length=1, max_length=32)
    framework_version: str | None = Field(default=None, min_length=1, max_length=32)
    python_version: str | None = Field(default=None, min_length=1, max_length=16)
    cuda_version: str | None = Field(default=None, min_length=1, max_length=16)
    image_ref: str | None = Field(default=None, min_length=3, max_length=256)
    prewarm_enabled: bool | None = None
    sort: int | None = Field(default=None, ge=0, le=9999)

    @field_validator("image_ref")
    @classmethod
    def _valid_ref(cls, v: str | None) -> str | None:
        return None if v is None else _check_image_ref(v)


class SkuImpactOut(BaseModel):
    """改价影响面:该 SKU 活跃(creating/starting/running)实例数/用户数/卡数。"""

    sku_id: int
    active_instances: int
    active_users: int
    active_gpus: int


class ImageCoverageOut(BaseModel):
    """预热覆盖:cached/total 节点数与百分比(total=巡检登记的目标节点数)。"""

    cached: int
    total: int
    pct: int


class AdminImageOut(ImageOut):
    """公开目录字段 + 管理端预热视图。"""

    prewarm_enabled: bool
    sort: int
    coverage: ImageCoverageOut
    failed_nodes: int


class ImageNodeCacheOut(BaseModel):
    node_name: str
    status: str
    last_error: str | None
    checked_at: datetime | None
    updated_at: datetime

    model_config = {"from_attributes": True}


class PrewarmEnqueuedOut(BaseModel):
    enqueued: int


class CapacityWarningOut(BaseModel):
    """结构化警示(前端按 code 映射文案,params 供插值)。"""

    code: Literal["unrecognized_model", "no_ready_node", "vram_exceeds_node"]
    params: dict[str, Any] = {}


class CapacityPreviewOut(BaseModel):
    """SKU 表单容量预览(纯台账推算)。"""

    matching_nodes: int
    ready_gpus: int
    total_gpus: int
    est_instances: int
    warnings: list[CapacityWarningOut]
