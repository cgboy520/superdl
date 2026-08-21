from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.core.money import MoneyOut


class PaymentChannelsOut(BaseModel):
    """可用支付渠道(充值弹窗按此启用 Tab)。"""

    wechat: bool
    alipay: bool
    mock: bool


class SiteConfigOut(BaseModel):
    """站点公开配置(页脚备案号等,未登录可访问)。"""

    icp_number: str | None
    police_record_number: str | None
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
    available_count: int = 0  # 近似库存(30s 缓存),service 填充

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
    oversell_vram: MoneyOut
    pool_label: str
    vcpu: int
    mem_gb: int
    disk_gb: int
    price_hourly: MoneyOut
    max_gpus_per_instance: int
    cuda_max: str | None
    status: str
    created_at: datetime
    # 台账/占用组装列(仅列表端点填充;写操作响应保持默认,前端保存后 refetch)
    capacity_gpus: int = 0  # 匹配「型号×池」的 Ready 物理卡数
    sold_share: str | None = None  # 已售算力 ÷ 可售总算力(含超卖),台账空为 None
    actual_oversell: str | None = None  # 已售算力 ÷ 物理算力,对照 oversell_cores 看余量

    model_config = {"from_attributes": True}


class SkuCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    gpu_model: str = Field(min_length=1, max_length=32)
    tier: str = Field(pattern="^(dedicated|mig|shared_std|shared_eco)$")
    mig_profile: str | None = None
    gpu_cores_pct: int = Field(default=100, ge=1, le=100)
    vram_gb: int = Field(ge=1)
    oversell_cores: Decimal = Field(default=Decimal("1.00"), ge=Decimal("1.00"), le=Decimal("9.99"))
    oversell_vram: Decimal = Field(default=Decimal("1.00"), ge=Decimal("1.00"), le=Decimal("9.99"))
    pool_label: str = Field(min_length=1, max_length=32)
    vcpu: int = Field(ge=1)
    mem_gb: int = Field(ge=1)
    disk_gb: int = Field(default=100, ge=10)
    price_hourly: Decimal = Field(gt=Decimal("0"))
    max_gpus_per_instance: int = Field(default=1, ge=1, le=8)
    cuda_max: str | None = None


class SkuUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    gpu_cores_pct: int | None = Field(default=None, ge=1, le=100)
    vram_gb: int | None = Field(default=None, ge=1)
    oversell_cores: Decimal | None = Field(default=None, ge=Decimal("1.00"), le=Decimal("9.99"))
    oversell_vram: Decimal | None = Field(default=None, ge=Decimal("1.00"), le=Decimal("9.99"))
    pool_label: str | None = None
    vcpu: int | None = Field(default=None, ge=1)
    mem_gb: int | None = Field(default=None, ge=1)
    disk_gb: int | None = Field(default=None, ge=10)
    price_hourly: Decimal | None = Field(default=None, gt=Decimal("0"))
    max_gpus_per_instance: int | None = Field(default=None, ge=1, le=8)
    cuda_max: str | None = None
    status: str | None = Field(default=None, pattern="^(on|off)$")


class ImageOut(BaseModel):
    id: int
    framework: str
    framework_version: str
    python_version: str
    cuda_version: str
    image_ref: str
    # 计算值:prewarm_enabled 且节点覆盖率达标(无缓存行时回落为 prewarm_enabled)
    is_prewarmed: bool

    model_config = {"from_attributes": True}


class ImageCreate(BaseModel):
    framework: str = Field(min_length=1, max_length=32)
    framework_version: str = Field(min_length=1, max_length=32)
    python_version: str = Field(min_length=1, max_length=16)
    cuda_version: str = Field(min_length=1, max_length=16)
    image_ref: str = Field(min_length=3, max_length=256)
    prewarm_enabled: bool = True
    sort: int = Field(default=0, ge=0, le=9999)


class ImageUpdate(BaseModel):
    framework: str | None = Field(default=None, min_length=1, max_length=32)
    framework_version: str | None = Field(default=None, min_length=1, max_length=32)
    python_version: str | None = Field(default=None, min_length=1, max_length=16)
    cuda_version: str | None = Field(default=None, min_length=1, max_length=16)
    image_ref: str | None = Field(default=None, min_length=3, max_length=256)
    prewarm_enabled: bool | None = None
    sort: int | None = Field(default=None, ge=0, le=9999)
