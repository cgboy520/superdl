from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.gpu_adapter import TIER_CPU, TIERS
from app.core.messages import render_message
from app.core.money import MoneyOut
from app.core.registry import is_valid_image_ref

# 档位枚举的事实源在 core/gpu_adapter,这里只拼正则——两处各写一遍必然漂
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
    # 经营主体(《电子商务法》第十五条;留空 = 前端不展示该行)
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
    # 池标签:同一 (pool, model) 物理池上的互斥档位可售数不可相加(前端按组取 max 展示)
    pool_label: str
    period_enabled: bool  # 是否接受包周期下单(市场页据此决定包日/包周/包月/包年 chips)
    spot_enabled: bool  # 是否上竞价档(市场页据此决定竞价入口可不可选)
    available_count: int = 0  # 近似库存(节点台账口径,每请求直接算),service 填充

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
    # 台账/占用组装列(仅列表端点填充;写操作响应保持默认,前端保存后 refetch)
    capacity_gpus: int = 0  # 匹配「型号×池」的 Ready 物理卡数
    sold_share: str | None = None  # 已售算力 ÷ 可售总算力(含超卖),台账空为 None
    actual_oversell: str | None = None  # 已售算力 ÷ 物理算力,对照 oversell_cores 看余量

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
    """档位与「带不带卡」的跨字段规则,返回文案键(None=通过)。

    规则只写这一处:SkuCreate 在契约层调用(422),SkuUpdate 是部分更新拿不到终态,
    由 service 合并出终态后调用(400 + message_key)。两处各写一遍必然漂。
    CPU 规格三项恒 0 + 型号空串 + 无切片,是 build_gpu_request 走 CPU 分支的前提
    (gpu_count 由 max_gpus_per_instance=0 逼成 0);GPU 规格反过来三项都不能为 0,
    否则会建出「0 显存 / 0 算力份额」的 HAMi SKU,下发即 Pending。
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
    # 下界放开到 0 是给 CPU 档留位置(gpu_model 空串、三项为 0);档位与这几项的配对由
    # 下面的 model_validator 兜住,GPU 档一项都不许为 0
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
    # 与 pool_label 成对可改(改池必须能同时清/填切片,否则 mig↔hami 两个方向都走不通);
    # 只在下架态放行,见 service.admin_update_sku
    mig_profile: str | None = None
    # 与 SkuCreate 同理放开到 0(CPU 档);终态配对在 service.admin_update_sku 复核
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
    # 必填原因:改价单人一步生效且被新实例快照;配套记录旧值与幅度超阈告警。同策略参数 PUT。
    reason: str = Field(min_length=2, max_length=200)


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


def _check_image_ref(v: str) -> str:
    """形态非法必须在管理端写入时就拒掉:目录 ref 钉 digest 后是 70+ 字符的手抄串,
    抄错一位若能入库,要等用户创建实例才报错。"""
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
