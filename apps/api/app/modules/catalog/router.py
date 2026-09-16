from fastapi import APIRouter

from app.core.compliance import current_profile
from app.core.config import get_settings
from app.core.db import DbSession
from app.core.platform_config import get_runtime_config
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
    """站点公开配置:备案号 + 可用支付渠道 + 部署身份(合规档位 / 币种 / 计费时区)(免登录)。"""
    cfg = await get_runtime_config(session)
    s = get_settings()
    profile = current_profile()
    return SiteConfigOut(
        icp_number=cfg.icp_number or None,
        police_record_number=cfg.police_record_number or None,
        support_email=cfg.support_email or None,
        support_wechat=cfg.support_wechat or None,
        company_name=cfg.company_name or None,
        company_address=cfg.company_address or None,
        company_phone=cfg.company_phone or None,
        business_license_url=cfg.business_license_url or None,
        payment_channels=PaymentChannelsOut(
            wechat=cfg.payment_wechat_enabled,
            alipay=cfg.payment_alipay_enabled,
            mock=s.payment_mock,
        ),
        compliance_profile=profile.name,
        phone_required=profile.phone_required,
        phone_dial_codes=list(profile.phone_dial_codes),
        kyc_form=profile.kyc_form,
        default_locale=profile.default_locale,
        currency=s.platform_currency,
        billing_timezone=s.billing_timezone,
    )


@router.get("/skus")
async def list_skus(
    session: DbSession, tier: str | None = None, gpu_model: str | None = None
) -> list[SkuMarketOut]:
    """算力市场:仅在架 SKU,含近似库存。未登录可访问。"""
    return await service.list_market_skus(session, tier, gpu_model)


@router.get("/images")
async def list_images(session: DbSession) -> list[ImageOut]:
    """平台镜像目录;is_prewarmed 为计算值(节点覆盖率达标)。"""
    return await service.list_images_out(session)
