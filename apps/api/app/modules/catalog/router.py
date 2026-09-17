from fastapi import APIRouter

from app.core.compliance import current_profile
from app.core.config import get_settings
from app.core.db import DbSession
from app.core.platform_config import get_runtime_config
from app.modules.billing import service as billing_service
from app.modules.catalog import service
from app.modules.catalog.schemas import (
    ImageOut,
    PaymentChannelOut,
    SiteConfigOut,
    SkuMarketOut,
)

router = APIRouter(tags=["catalog"])


@router.get("/site-config")
async def get_site_config(session: DbSession) -> SiteConfigOut:
    """Public site configuration: filing numbers + enabled payment channels + deployment identity
    (compliance profile / currency / billing time zone) (no login)."""
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
        payment_channels=[
            PaymentChannelOut(name=spec.name, presentation=spec.presentation)
            for spec in billing_service.enabled_payment_channels(cfg)
        ],
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
    """Market: listed SKUs only, with approximate stock. Available without login."""
    return await service.list_market_skus(session, tier, gpu_model)


@router.get("/images")
async def list_images(session: DbSession) -> list[ImageOut]:
    """Platform image catalog; is_prewarmed is computed (node coverage meets the threshold)."""
    return await service.list_images_out(session)
