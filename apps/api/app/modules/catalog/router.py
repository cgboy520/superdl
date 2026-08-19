from fastapi import APIRouter

from app.core.db import DbSession
from app.modules.catalog import service
from app.modules.catalog.schemas import ImageOut, SkuMarketOut

router = APIRouter(tags=["catalog"])


@router.get("/skus")
async def list_skus(
    session: DbSession, tier: str | None = None, gpu_model: str | None = None
) -> list[SkuMarketOut]:
    """算力市场:仅在架 SKU,含近似库存(30s 缓存)。未登录可访问。"""
    return await service.list_market_skus(session, tier, gpu_model)


@router.get("/images")
async def list_images(session: DbSession) -> list[ImageOut]:
    """平台镜像目录(框架→版本→Python→CUDA 级联数据源)。"""
    images = await service.list_images(session)
    return [ImageOut.model_validate(i) for i in images]
