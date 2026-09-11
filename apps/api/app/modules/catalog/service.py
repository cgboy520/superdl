from collections.abc import Iterable
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from fastapi import status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode, conflict, not_found
from app.core.gpu_adapter import (
    POOL_CPU,
    POOL_HAMI,
    POOL_MIG,
    TIER_CPU,
    TIER_POOLS,
    TIER_SHARED,
)
from app.core.gpu_models import canonical_gpu_model
from app.core.logging import get_logger
from app.core.money import as_amount, as_price
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
    cpu_spec_error,
)

if TYPE_CHECKING:
    from app.modules.nodes.models import NodeSpec

logger = get_logger(__name__)


def sellable_per_gpu(pool_label: str, gpu_cores_pct: int, oversell_cores: Decimal) -> int:
    """每张物理卡可售实例数:hami 池 = ⌊100 × oversell_cores ÷ gpu_cores_pct⌋(Decimal,至少 1),
    kata / mig 池恒 1。市场库存、创建软准入、容量预览共用。
    """
    if pool_label != POOL_HAMI:
        return 1
    return max(1, int(Decimal(100) * oversell_cores // max(1, gpu_cores_pct)))


def sellable_cpu_slots(
    vcpu: int, mem_gb: int, specs: Iterable["NodeSpec"], *, gpu_node_vcpu_cap: int
) -> int:
    """CPU 规格的近似可售实例数:逐 Ready 节点取 min(⌊预算 vCPU ÷ vcpu⌋, ⌊预算内存 ÷ mem_gb⌋) 求和。
    预算:cpu 池整机;其它池每节点封顶 `gpu_node_cpu_instance_vcpu_cap` 核,内存同比例,cap=0 不卖。
    上限估算,不扣已用。
    """
    if vcpu <= 0 or mem_gb <= 0:
        return 0
    total = 0
    for node in specs:
        if node.status != "Ready" or node.vcpu <= 0 or node.mem_gb <= 0:
            continue
        if node.pool_label == POOL_CPU:
            vcpu_budget, mem_budget = node.vcpu, node.mem_gb
        else:
            vcpu_budget = min(gpu_node_vcpu_cap, node.vcpu)
            mem_budget = node.mem_gb * vcpu_budget // node.vcpu
        total += min(vcpu_budget // vcpu, mem_budget // mem_gb)
    return total


def _check_tier_pool(tier: str, pool_label: str, mig_profile: str | None) -> None:
    """档位与池必须配对(core/gpu_adapter),mig 切片与 mig 池同有同无;
    共享档叠加 SUPERDL_SHARED_TIER_ALLOWED_POOLS(空即停售)。建 SKU 与改池共用。
    """
    allowed = TIER_POOLS.get(tier, ())
    if tier == TIER_SHARED:
        from app.core.config import get_settings

        allowed = tuple(p for p in allowed if p in get_settings().parsed_shared_tier_pools())
    if pool_label not in allowed:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="catalog.tierPoolMismatch",
            params={"tier": tier, "pools": "/".join(allowed) or "-", "pool": pool_label},
        )
    if (pool_label == POOL_MIG) != bool(mig_profile):
        raise AppError(ErrorCode.VALIDATION_ERROR, key="catalog.migProfileMismatch")


async def list_market_skus(
    session: AsyncSession, tier: str | None = None, gpu_model: str | None = None
) -> list[SkuMarketOut]:
    stmt = select(Sku).where(Sku.status == "on").order_by(Sku.price_hourly)
    if tier:
        stmt = stmt.where(Sku.tier == tier)
    if gpu_model:
        stmt = stmt.where(Sku.gpu_model == gpu_model)
    skus = list((await session.execute(stmt)).scalars().all())
    counts = await inventory.get_available_counts(session, skus)  # 批量一次,免 N+1 扇出
    out: list[SkuMarketOut] = []
    for sku in skus:
        item = SkuMarketOut.model_validate(sku)
        item.available_count = counts.get(sku.id, 0)
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


async def is_catalog_image(session: AsyncSession, image_ref: str) -> bool:
    """该镜像引用是否属于平台镜像目录。"""
    return (
        await session.execute(select(PlatformImage.id).where(PlatformImage.image_ref == image_ref))
    ).scalar_one_or_none() is not None


async def image_coverage(session: AsyncSession) -> dict[int, tuple[int, int, int]]:
    """预热覆盖聚合:image_id → (cached 节点数, 总行数, failed 节点数)。"""
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


def _checked_price(value: Decimal) -> Decimal:
    """单价走 money.as_price(4 位),量化后为 0 拒绝;
    按小时计费的 SKU 强制 2 位(price == as_amount(price))。"""
    price = as_price(value)
    if price <= 0:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="catalog.priceTooSmall")
    if price != as_amount(price):
        raise AppError(ErrorCode.VALIDATION_ERROR, key="catalog.priceHourlyTwoDecimals")
    return price


async def admin_create_sku(session: AsyncSession, data: SkuCreate) -> Sku:
    values = data.model_dump()
    values["price_hourly"] = _checked_price(values["price_hourly"])
    _check_tier_pool(values["tier"], values["pool_label"], values.get("mig_profile"))
    sku = Sku(**values)
    session.add(sku)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise conflict(key="catalog.skuBusinessKeyExists") from exc
    await session.refresh(sku)
    return sku


# 单次改价幅度超过这个比例即告警,不阻断
PRICE_CHANGE_ALERT_RATIO = Decimal("0.5")


async def admin_update_sku(
    session: AsyncSession, sku_id: int, data: SkuUpdate, *, force: bool = False
) -> tuple[Sku, dict[str, Any]]:
    """更新 SKU。返回 (sku, 本次实际变更字段的旧值快照),旧值交调用方落审计。"""
    sku = await get_sku(session, sku_id)
    was_on_sale = sku.status == "on"
    updates = data.model_dump(exclude_unset=True, exclude={"reason"})
    # 改池或改切片只在下架态放行;在应用更新之前判
    if was_on_sale and any(
        field in updates and updates[field] != getattr(sku, field)
        for field in ("pool_label", "mig_profile")
    ):
        raise conflict(key="catalog.isolationChangeNeedsOffSale")
    if updates.get("price_hourly") is not None:
        updates["price_hourly"] = _checked_price(updates["price_hourly"])
    turning_on = updates.get("status") == "on" and sku.status != "on"
    before: dict[str, Any] = {}
    for field, value in updates.items():
        old = getattr(sku, field)
        if old != value:
            before[field] = str(old) if isinstance(old, Decimal) else old
        setattr(sku, field, value)
    # 池与切片任一动了都复核配对
    if "pool_label" in before or "mig_profile" in before:
        _check_tier_pool(sku.tier, sku.pool_label, sku.mig_profile)
    # 「带不带卡」规则按合并后的终态复核
    cpu_key = cpu_spec_error(
        tier=sku.tier,
        gpu_model=sku.gpu_model,
        gpu_cores_pct=sku.gpu_cores_pct,
        vram_gb=sku.vram_gb,
        max_gpus_per_instance=sku.max_gpus_per_instance,
        mig_profile=sku.mig_profile,
    )
    if cpu_key is not None:
        raise AppError(ErrorCode.VALIDATION_ERROR, key=cpu_key)
    if turning_on and not force:
        await _ensure_sellable(session, sku)
    if "price_hourly" in before:
        await _alert_large_price_change(
            session, sku, Decimal(before["price_hourly"]), updates["price_hourly"], data.reason
        )
    # 业务唯一键冲突 → 409
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise conflict(key="catalog.skuBusinessKeyExists") from exc
    await session.refresh(sku)
    return sku, before


async def _alert_large_price_change(
    session: AsyncSession, sku: Sku, old: Decimal, new: Decimal, reason: str
) -> None:
    """大幅改价落一条管理端告警,不阻断。"""
    ratio = abs(new - old) / old
    if ratio < PRICE_CHANGE_ALERT_RATIO:
        return
    from app.modules.notify import service as notify_service

    logger.warning(
        "sku_price_large_change", sku_id=sku.id, old=str(old), new=str(new), reason=reason
    )
    await notify_service.notify(
        session,
        None,  # user_id=None → 平台告警流(管理端总览右栏)
        type_="admin_alert",
        title=f"SKU 单价大幅调整:{sku.name}",
        content=f"{old} → {new} 元/时(幅度 {ratio:.0%});原因:{reason}",
        severity="warning",
        dedup_key=f"sku_price:{sku.id}:{new}",
    )


async def _ensure_sellable(session: AsyncSession, sku: Sku) -> None:
    """上架硬校验:台账须有「型号×池」匹配的 Ready 节点;未识别型号只能 force 上架;CPU 档只校验池。"""
    from app.modules.nodes import service as nodes_service

    specs = await nodes_service.ready_specs(session)
    if sku.tier == TIER_CPU:
        if nodes_service.pool_specs(specs, sku.pool_label):
            return
        raise AppError(
            ErrorCode.SKU_NOT_SELLABLE,
            key="catalog.skuNotSellableCpu",
            params={"pool": sku.pool_label},
            http_status=status.HTTP_409_CONFLICT,
        )
    wanted = canonical_gpu_model(sku.gpu_model)
    if nodes_service.matching_specs(specs, sku.pool_label, wanted):
        return
    raise AppError(
        ErrorCode.SKU_NOT_SELLABLE,
        key="catalog.skuNotSellable",
        params={"model": wanted or sku.gpu_model, "pool": sku.pool_label},
        http_status=status.HTTP_409_CONFLICT,
    )


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
        raise conflict(key="catalog.imageRefExists") from exc
    await session.refresh(img)
    return img


async def admin_update_image(
    session: AsyncSession, image_id: int, data: ImageUpdate
) -> PlatformImage:
    img = await get_image(session, image_id)
    updates = data.model_dump(exclude_unset=True)
    if updates.get("image_ref") and updates["image_ref"] != img.image_ref:
        # ref 变更:同事务清缓存行
        await session.execute(delete(ImageNodeCache).where(ImageNodeCache.image_id == image_id))
    for field, value in updates.items():
        setattr(img, field, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        raise conflict(key="catalog.imageRefExists") from exc
    await session.refresh(img)
    return img


async def admin_delete_image(session: AsyncSession, image_id: int) -> None:
    """删除目录条目(cache 行 FK CASCADE);运行中实例不受影响。"""
    img = await get_image(session, image_id)
    await session.delete(img)
    await session.commit()


async def admin_prewarm_image(session: AsyncSession, image_id: int) -> int:
    """立即预热:非 cached 行置 pending 并同事务 enqueue,返回入队数。"""
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
