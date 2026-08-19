from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode, not_found
from app.modules.catalog import inventory
from app.modules.catalog.models import PlatformImage, Sku
from app.modules.catalog.schemas import SkuCreate, SkuMarketOut, SkuUpdate


async def list_market_skus(
    session: AsyncSession, tier: str | None = None, gpu_model: str | None = None
) -> list[SkuMarketOut]:
    stmt = select(Sku).where(Sku.status == "on").order_by(Sku.price_hourly)
    if tier:
        stmt = stmt.where(Sku.tier == tier)
    if gpu_model:
        stmt = stmt.where(Sku.gpu_model == gpu_model)
    skus = (await session.execute(stmt)).scalars().all()
    out: list[SkuMarketOut] = []
    for sku in skus:
        item = SkuMarketOut.model_validate(sku)
        item.available_count = await inventory.get_available_count(sku)
        out.append(item)
    return out


async def get_sku(session: AsyncSession, sku_id: int) -> Sku:
    sku = await session.get(Sku, sku_id)
    if sku is None:
        raise not_found("规格不存在")
    return sku


async def get_on_sale_sku(session: AsyncSession, sku_id: int) -> Sku:
    """下单入口:必须在架。"""
    sku = await get_sku(session, sku_id)
    if sku.status != "on":
        raise AppError(ErrorCode.SKU_NOT_ON_SALE, "该规格已下架")
    return sku


async def list_images(session: AsyncSession) -> list[PlatformImage]:
    return list(
        (
            await session.execute(
                select(PlatformImage).order_by(
                    PlatformImage.framework,
                    PlatformImage.sort,
                    PlatformImage.framework_version.desc(),
                )
            )
        ).scalars()
    )


async def get_image_by_ref(session: AsyncSession, image_ref: str) -> PlatformImage | None:
    return (
        await session.execute(select(PlatformImage).where(PlatformImage.image_ref == image_ref))
    ).scalar_one_or_none()


# ---------- 管理端 ----------


async def admin_list_skus(session: AsyncSession) -> list[Sku]:
    return list((await session.execute(select(Sku).order_by(Sku.id))).scalars())


async def admin_create_sku(session: AsyncSession, data: SkuCreate) -> Sku:
    sku = Sku(**data.model_dump())
    session.add(sku)
    await session.commit()
    await session.refresh(sku)
    return sku


async def admin_update_sku(session: AsyncSession, sku_id: int, data: SkuUpdate) -> Sku:
    sku = await get_sku(session, sku_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(sku, field, value)
    await session.commit()
    await session.refresh(sku)
    inventory.clear_cache()
    return sku
