from fastapi import APIRouter

from app.core.config import get_settings
from app.core.db import DbSession
from app.core.platform_config import get_effective_platform_config
from app.modules.catalog import service
from app.modules.catalog.schemas import (
    ImageOut,
    PaymentChannelsOut,
    SiteConfigOut,
    SkuMarketOut,
)

router = APIRouter(tags=["catalog"])


@router.get("/site-config")
async def get_site_config(session: DbSession) -> SiteConfigOut:
    """站点公开配置:备案号 + 可用支付渠道(页脚/充值弹窗动态渲染;免登录)。"""
    cfg = await get_effective_platform_config(session)
    s = get_settings()
    return SiteConfigOut(
        icp_number=cfg["icp_number"] or None,
        police_record_number=cfg["police_record_number"] or None,
        support_email=cfg["support_email"] or None,
        support_wechat=cfg["support_wechat"] or None,
        payment_channels=PaymentChannelsOut(
            wechat=cfg["payment_wechat_enabled"] == "true",
            alipay=cfg["payment_alipay_enabled"] == "true",
            mock=s.payment_mock and s.environment != "prod",
        ),
    )


@router.get("/skus")
async def list_skus(
    session: DbSession, tier: str | None = None, gpu_model: str | None = None
) -> list[SkuMarketOut]:
    """算力市场:仅在架 SKU,含近似库存(30s 缓存)。未登录可访问。"""
    return await service.list_market_skus(session, tier, gpu_model)


@router.get("/images")
async def list_images(session: DbSession) -> list[ImageOut]:
    """平台镜像目录(框架→版本→Python→CUDA 级联数据源)。
    is_prewarmed 为真实计算值(节点覆盖率达标才标「预热镜像,秒级启动」)。"""
    return await service.list_images_out(session)
