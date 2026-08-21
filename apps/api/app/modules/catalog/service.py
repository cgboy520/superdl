from fastapi import status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode, not_found
from app.core.outbox import enqueue
from app.core.policies import get_effective_policies
from app.modules.catalog import inventory
from app.modules.catalog.models import ImageNodeCache, PlatformImage, Sku
from app.modules.catalog.schemas import (
    ImageCreate,
    ImageOut,
    ImageUpdate,
    SkuCreate,
    SkuMarketOut,
    SkuUpdate,
)


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
        raise AppError(ErrorCode.SKU_NOT_ON_SALE, key="catalog.skuOffSale")
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


async def image_coverage(session: AsyncSession) -> dict[int, tuple[int, int, int]]:
    """预热覆盖聚合:image_id → (cached 节点数, 总行数, failed 节点数)。纯 DB,不调 K8s。"""
    rows = (
        await session.execute(
            select(
                ImageNodeCache.image_id,
                func.count().filter(ImageNodeCache.status == "cached"),
                func.count(),
                func.count().filter(ImageNodeCache.status == "failed"),
            ).group_by(ImageNodeCache.image_id)
        )
    ).all()
    return {image_id: (cached, total, failed) for image_id, cached, total, failed in rows}


async def list_images_out(session: AsyncSession) -> list[ImageOut]:
    """公开镜像目录,is_prewarmed 为计算值:
    prewarm_enabled 且(零 cache 行回落旧语义 / 覆盖率 ≥ prewarm_min_coverage_pct)。"""
    images = await list_images(session)
    coverage = await image_coverage(session)
    threshold = (await get_effective_policies(session)).prewarm_min_coverage_pct
    out: list[ImageOut] = []
    for img in images:
        cached, total, _failed = coverage.get(img.id, (0, 0, 0))
        prewarmed = img.prewarm_enabled and (total == 0 or cached * 100 >= threshold * total)
        out.append(
            ImageOut(
                id=img.id,
                framework=img.framework,
                framework_version=img.framework_version,
                python_version=img.python_version,
                cuda_version=img.cuda_version,
                image_ref=img.image_ref,
                is_prewarmed=prewarmed,
            )
        )
    return out


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


# ---------- 管理端:镜像与预热 ----------


async def get_image(session: AsyncSession, image_id: int) -> PlatformImage:
    img = await session.get(PlatformImage, image_id)
    if img is None:
        raise not_found("镜像不存在")
    return img


async def admin_create_image(session: AsyncSession, data: ImageCreate) -> PlatformImage:
    img = PlatformImage(**data.model_dump())
    session.add(img)
    try:
        await session.commit()
    except IntegrityError as exc:
        raise AppError(
            ErrorCode.CONFLICT,
            key="catalog.imageRefExists",
            http_status=status.HTTP_409_CONFLICT,
        ) from exc
    await session.refresh(img)
    return img


async def admin_update_image(
    session: AsyncSession, image_id: int, data: ImageUpdate
) -> PlatformImage:
    img = await get_image(session, image_id)
    updates = data.model_dump(exclude_unset=True)
    if updates.get("image_ref") and updates["image_ref"] != img.image_ref:
        # ref 变更 = 缓存作废:同事务清行,巡检按新 ref 重建
        await session.execute(delete(ImageNodeCache).where(ImageNodeCache.image_id == image_id))
    for field, value in updates.items():
        setattr(img, field, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        raise AppError(
            ErrorCode.CONFLICT,
            key="catalog.imageRefExists",
            http_status=status.HTTP_409_CONFLICT,
        ) from exc
    await session.refresh(img)
    return img


async def admin_delete_image(session: AsyncSession, image_id: int) -> None:
    """删除目录条目(cache 行 FK CASCADE)。运行中实例存的是 image_ref 快照,不受影响。"""
    img = await get_image(session, image_id)
    await session.delete(img)
    await session.commit()


async def admin_prewarm_image(session: AsyncSession, image_id: int) -> int:
    """立即预热:非 cached 行置 pending 并同事务 enqueue(硬规范 #3)。
    不在请求路径调 K8s;新节点行由巡检铺(≤60s)。返回入队数。"""
    img = await get_image(session, image_id)
    if not img.prewarm_enabled:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="catalog.prewarmDisabled")
    rows = (
        await session.execute(
            select(ImageNodeCache).where(
                ImageNodeCache.image_id == image_id, ImageNodeCache.status != "cached"
            )
        )
    ).scalars()
    n = 0
    for row in rows:
        row.status = "pending"
        enqueue(session, "image.prewarm", {"image_id": image_id, "node_name": row.node_name})
        n += 1
    await session.commit()
    return n


async def image_node_rows(session: AsyncSession, image_id: int) -> list[ImageNodeCache]:
    await get_image(session, image_id)  # 404 门禁
    return list(
        (
            await session.execute(
                select(ImageNodeCache)
                .where(ImageNodeCache.image_id == image_id)
                .order_by(ImageNodeCache.node_name)
            )
        ).scalars()
    )
