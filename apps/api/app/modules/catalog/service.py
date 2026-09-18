from collections.abc import Iterable
from datetime import timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING, Any

from fastapi import status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.config import get_settings
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
from app.core.money import as_amount, as_price, price_label
from app.core.outbox import enqueue
from app.core.platform_config import get_runtime_config
from app.core.servercopy import copy as server_copy
from app.core.timeutil import now_utc
from app.modules.catalog import inventory
from app.modules.catalog.models import ImageNodeCache, PlatformImage, Sku
from app.modules.catalog.schemas import (
    AdminImageOut,
    CapacityPreviewOut,
    CapacityWarningOut,
    ImageCoverageOut,
    ImageCreate,
    ImageOut,
    ImageUpdate,
    SkuAdminOut,
    SkuCreate,
    SkuMarketOut,
    SkuUpdate,
    cpu_spec_error,
)
from app.modules.nodes import service as nodes_service
from app.modules.notify import service as notify_service
from app.modules.orchestrator import ports as orchestrator_ports

if TYPE_CHECKING:
    from app.modules.nodes.models import NodeSpec

logger = get_logger(__name__)


def sellable_per_gpu(pool_label: str, gpu_cores_pct: int, oversell_cores: Decimal) -> int:
    """Sellable instances per card in the hami pool = max(1, ⌊100×oversell_cores/max(1,
    gpu_cores_pct)⌋), 1 in the other pools."""
    if pool_label != POOL_HAMI:
        return 1
    return max(1, int(Decimal(100) * oversell_cores // max(1, gpu_cores_pct)))


def sellable_cpu_slots(
    vcpu: int, mem_gb: int, specs: Iterable["NodeSpec"], *, gpu_node_vcpu_cap: int
) -> int:
    """Approximate sellable CPU-SKU instances: per Ready node min(⌊budget vCPU ÷ vcpu⌋, ⌊budget
    memory
    ÷ mem_gb⌋), summed.
    Budget: the whole machine in the cpu pool; other pools capped per node at
    `gpu_node_cpu_instance_vcpu_cap` cores with memory scaled alike, cap=0 sells none.
    Upper-bound estimate, usage is not subtracted.
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
    """Tier and pool must pair (core/gpu_adapter), the mig slice and the mig pool are present or
    absent together;
    the shared tier is further gated by SUPERDL_SHARED_TIER_ALLOWED_POOLS (empty = not sold). Shared
    by SKU creation and pool changes.
    """
    allowed = TIER_POOLS.get(tier, ())
    if tier == TIER_SHARED:
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
    counts = await inventory.get_available_counts(session, skus)
    out: list[SkuMarketOut] = []
    for sku in skus:
        item = SkuMarketOut.model_validate(sku)
        item.available_count = counts.get(sku.id, 0)
        out.append(item)
    return out


async def get_sku(session: AsyncSession, sku_id: int) -> Sku:
    sku = await session.get(Sku, sku_id)
    if sku is None:
        raise not_found(key="catalog.skuNotFound")
    return sku


async def get_on_sale_sku(session: AsyncSession, sku_id: int) -> Sku:
    """Order entry: must be listed."""
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
    """Whether the image reference belongs to the platform image catalog."""
    return (
        await session.execute(select(PlatformImage.id).where(PlatformImage.image_ref == image_ref))
    ).scalar_one_or_none() is not None


async def image_coverage(session: AsyncSession) -> dict[int, tuple[int, int, int]]:
    """Prewarm coverage aggregate: image_id → (cached nodes, total rows, failed nodes)."""
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


def _is_prewarmed(img: PlatformImage, cached: int, total: int, threshold: int) -> bool:
    """True when prewarm is enabled and (no cache rows or coverage percentage reaches threshold)."""
    return img.prewarm_enabled and (total == 0 or cached * 100 >= threshold * total)


async def list_images_out(session: AsyncSession) -> list[ImageOut]:
    """Public image catalog, is_prewarmed is computed."""
    images = await list_images(session)
    coverage = await image_coverage(session)
    threshold = (await get_runtime_config(session)).prewarm_min_coverage_pct
    out: list[ImageOut] = []
    for img in images:
        cached, total, _failed = coverage.get(img.id, (0, 0, 0))
        out.append(
            ImageOut(
                id=img.id,
                framework=img.framework,
                framework_version=img.framework_version,
                python_version=img.python_version,
                cuda_version=img.cuda_version,
                image_ref=img.image_ref,
                is_prewarmed=_is_prewarmed(img, cached, total, threshold),
            )
        )
    return out


async def admin_image_out(session: AsyncSession, img: PlatformImage) -> AdminImageOut:
    """Admin view of one image (create / update response)."""
    coverage = await image_coverage(session)
    threshold = (await get_runtime_config(session)).prewarm_min_coverage_pct
    return _admin_image_out(img, coverage, threshold)


async def admin_list_images_out(session: AsyncSession) -> list[AdminImageOut]:
    """Image catalog + prewarm coverage per image (pure DB aggregate, no K8s call)."""
    images = await list_images(session)
    coverage = await image_coverage(session)
    threshold = (await get_runtime_config(session)).prewarm_min_coverage_pct
    return [_admin_image_out(img, coverage, threshold) for img in images]


def _admin_image_out(
    img: PlatformImage, coverage: dict[int, tuple[int, int, int]], threshold: int
) -> AdminImageOut:
    cached, total, failed = coverage.get(img.id, (0, 0, 0))
    return AdminImageOut(
        id=img.id,
        framework=img.framework,
        framework_version=img.framework_version,
        python_version=img.python_version,
        cuda_version=img.cuda_version,
        image_ref=img.image_ref,
        is_prewarmed=_is_prewarmed(img, cached, total, threshold),
        prewarm_enabled=img.prewarm_enabled,
        sort=img.sort,
        coverage=ImageCoverageOut(
            cached=cached, total=total, pct=(cached * 100 // total) if total else 0
        ),
        failed_nodes=failed,
    )


async def admin_list_skus(session: AsyncSession) -> list[Sku]:
    return list((await session.execute(select(Sku).order_by(Sku.id))).scalars())


def _checked_price(value: Decimal) -> Decimal:
    """Unit prices go through money.as_price (4 dp), zero after quantisation is rejected;
    hourly-billed SKUs are forced to 2 dp (price == as_amount(price))."""
    price = as_price(value)
    if price <= 0:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="catalog.priceTooSmall")
    if price != as_amount(price):
        raise AppError(ErrorCode.VALIDATION_ERROR, key="catalog.priceHourlyTwoDecimals")
    return price


async def _commit_or_conflict(session: AsyncSession, *, key: str) -> None:
    """Commit the transaction; IntegrityError rolls back and becomes a 409 with the given copy."""
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise conflict(key=key) from exc


async def admin_create_sku(session: AsyncSession, data: SkuCreate) -> Sku:
    values = data.model_dump()
    values["price_hourly"] = _checked_price(values["price_hourly"])
    _check_tier_pool(values["tier"], values["pool_label"], values.get("mig_profile"))
    sku = Sku(**values)
    session.add(sku)
    await _commit_or_conflict(session, key="catalog.skuBusinessKeyExists")
    await session.refresh(sku)
    return sku


PRICE_CHANGE_ALERT_RATIO = Decimal("0.5")
PRICE_CHANGE_WINDOW = timedelta(hours=24)


async def admin_update_sku(
    session: AsyncSession, sku_id: int, data: SkuUpdate, *, force: bool = False
) -> tuple[Sku, dict[str, Any]]:
    """Update the SKU. Returns (sku, snapshot of the old values of the fields actually changed); the
    caller writes the old values to the audit."""
    sku = await get_sku(session, sku_id)
    was_on_sale = sku.status == "on"
    updates = data.model_dump(exclude_unset=True, exclude={"reason"})
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
    if "pool_label" in before or "mig_profile" in before:
        _check_tier_pool(sku.tier, sku.pool_label, sku.mig_profile)
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
    await _commit_or_conflict(session, key="catalog.skuBusinessKeyExists")
    await session.refresh(sku)
    return sku, before


async def _price_baseline_24h(session: AsyncSession, sku_id: int, fallback: Decimal) -> Decimal:
    """Old price of the earliest successful price-change audit row within 24 hours; the pre-change
    price when there is none."""
    before_price = AuditLog.detail["before"]["price_hourly"].astext
    stmt = (
        select(before_price)
        .where(
            AuditLog.actor_type == "admin",
            AuditLog.target == f"sku:{sku_id}",
            AuditLog.action.like("admin.PATCH %"),
            AuditLog.result < 400,
            AuditLog.created_at >= now_utc() - PRICE_CHANGE_WINDOW,
            before_price.isnot(None),
        )
        .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
        .limit(1)
    )
    raw = (await session.execute(stmt)).scalar_one_or_none()
    return Decimal(raw) if raw else fallback


async def _alert_large_price_change(
    session: AsyncSession, sku: Sku, old: Decimal, new: Decimal, reason: str
) -> None:
    """Cumulative ≥50 % against the 24-hour baseline writes a critical alert; otherwise a single
    step ≥50 % writes a warning. Never blocks."""
    baseline = await _price_baseline_24h(session, sku.id, old)
    cumulative = abs(new - baseline) / baseline
    step = abs(new - old) / old
    if cumulative >= PRICE_CHANGE_ALERT_RATIO:
        logger.warning(
            "sku_price_cumulative_change",
            sku_id=sku.id,
            baseline=str(baseline),
            old=str(old),
            new=str(new),
            reason=reason,
        )
        await notify_service.notify(
            session,
            None,
            type_="admin_alert",
            title=server_copy("catalog.price_change_24h.title", sku=sku.name),
            content=server_copy(
                "catalog.price_change_24h.content",
                baseline=price_label(baseline),
                new=price_label(new),
                pct=f"{cumulative:.0%}",
                old=price_label(old),
                new_raw=price_label(new),
                reason=reason,
            ),
            severity="critical",
            dedup_key=f"sku_price_24h:{sku.id}:{new}",
        )
        return
    if step < PRICE_CHANGE_ALERT_RATIO:
        return
    logger.warning(
        "sku_price_large_change", sku_id=sku.id, old=str(old), new=str(new), reason=reason
    )
    await notify_service.notify(
        session,
        None,
        type_="admin_alert",
        title=server_copy("catalog.price_change.title", sku=sku.name),
        content=server_copy(
            "catalog.price_change.content",
            old=price_label(old),
            new=price_label(new),
            pct=f"{step:.0%}",
            reason=reason,
        ),
        severity="warning",
        dedup_key=f"sku_price:{sku.id}:{new}",
    )


async def _ensure_sellable(session: AsyncSession, sku: Sku) -> None:
    """Listing hard check: the inventory must have a Ready node matching "model × pool";
    unrecognised
    models can only be force-listed; the CPU tier checks the pool only."""
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


async def get_image(session: AsyncSession, image_id: int) -> PlatformImage:
    img = await session.get(PlatformImage, image_id)
    if img is None:
        raise not_found(key="catalog.imageNotFound")
    return img


async def admin_create_image(session: AsyncSession, data: ImageCreate) -> PlatformImage:
    img = PlatformImage(**data.model_dump())
    session.add(img)
    await _commit_or_conflict(session, key="catalog.imageRefExists")
    await session.refresh(img)
    return img


async def admin_update_image(
    session: AsyncSession, image_id: int, data: ImageUpdate
) -> PlatformImage:
    img = await get_image(session, image_id)
    updates = data.model_dump(exclude_unset=True)
    if updates.get("image_ref") and updates["image_ref"] != img.image_ref:
        await session.execute(delete(ImageNodeCache).where(ImageNodeCache.image_id == image_id))
    for field, value in updates.items():
        setattr(img, field, value)
    await _commit_or_conflict(session, key="catalog.imageRefExists")
    await session.refresh(img)
    return img


async def admin_delete_image(session: AsyncSession, image_id: int) -> None:
    """Delete the catalog entry (cache rows FK CASCADE); running instances are unaffected."""
    img = await get_image(session, image_id)
    await session.delete(img)
    await session.commit()


async def admin_prewarm_image(session: AsyncSession, image_id: int) -> int:
    """Prewarm now: set non-cached rows to pending and enqueue in the same transaction, return the
    enqueued count."""
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
    await get_image(session, image_id)
    return list(
        (
            await session.execute(
                select(ImageNodeCache)
                .where(ImageNodeCache.image_id == image_id)
                .order_by(ImageNodeCache.node_name)
            )
        ).scalars()
    )


async def admin_skus_out(session: AsyncSession) -> list[SkuAdminOut]:
    """SKU list with inventory capacity and occupancy columns: actual_oversell / sold_share are the
    sold nominal compute (cards × pct/100)
    over physical and over sellable (× oversell), 2 dp."""
    skus = await admin_list_skus(session)
    specs = await nodes_service.ready_specs(session)
    sold = await orchestrator_ports.active_gpu_counts_by_sku(session)
    out: list[SkuAdminOut] = []
    for sku in skus:
        item = SkuAdminOut.model_validate(sku)
        wanted = canonical_gpu_model(sku.gpu_model)
        item.capacity_gpus = sum(
            sp.gpu_count for sp in nodes_service.matching_specs(specs, sku.pool_label, wanted)
        )
        if item.capacity_gpus:
            nominal = Decimal(sold.get(sku.id, 0)) * Decimal(sku.gpu_cores_pct) / Decimal(100)
            cap = Decimal(item.capacity_gpus)
            item.actual_oversell = str(_ratio(nominal / cap))
            item.sold_share = str(_ratio(nominal / (cap * sku.oversell_cores)))
        out.append(item)
    return out


RATIO_QUANT = Decimal("0.01")


def _ratio(value: Decimal) -> Decimal:
    """Two-decimal ratio for admin capacity output; currency-independent."""
    return value.quantize(RATIO_QUANT, rounding=ROUND_HALF_EVEN)


async def capacity_preview(
    session: AsyncSession,
    *,
    pool_label: str,
    gpu_model: str,
    gpu_cores_pct: int,
    oversell_cores: Decimal,
    vram_gb: int | None,
    vcpu: int | None,
    mem_gb: int | None,
) -> CapacityPreviewOut:
    """Live capacity preview of the SKU form. Empty gpu_model = CPU SKU: nodes matched by pool only,
    sellable count via sellable_cpu_slots."""
    all_specs = await nodes_service.list_node_specs(session)
    warnings: list[CapacityWarningOut] = []
    if not gpu_model:
        specs = nodes_service.pool_specs(all_specs, pool_label)
        ready = [sp for sp in specs if sp.status == "Ready"]
        if not ready:
            warnings.append(
                CapacityWarningOut(
                    code="no_ready_node", params={"model": "CPU", "pool": pool_label}
                )
            )
        est = 0
        if vcpu and mem_gb:
            cap = (await get_runtime_config(session)).gpu_node_cpu_instance_vcpu_cap
            est = sellable_cpu_slots(vcpu, mem_gb, ready, gpu_node_vcpu_cap=cap)
        return CapacityPreviewOut(
            matching_nodes=len(specs),
            ready_gpus=0,
            total_gpus=0,
            est_instances=est,
            warnings=warnings,
        )
    wanted = canonical_gpu_model(gpu_model)
    if wanted is None:
        warnings.append(CapacityWarningOut(code="unrecognized_model", params={"model": gpu_model}))
    specs = nodes_service.matching_specs(all_specs, pool_label, wanted)
    ready = [sp for sp in specs if sp.status == "Ready"]
    ready_gpus = sum(sp.gpu_count for sp in ready)
    if not ready:
        warnings.append(
            CapacityWarningOut(
                code="no_ready_node", params={"model": wanted or gpu_model, "pool": pool_label}
            )
        )
    max_vram = max((sp.vram_gb for sp in ready), default=0)
    if vram_gb is not None and ready and vram_gb > max_vram:
        warnings.append(
            CapacityWarningOut(
                code="vram_exceeds_node", params={"vram_gb": vram_gb, "node_vram_gb": max_vram}
            )
        )
    return CapacityPreviewOut(
        matching_nodes=len(specs),
        ready_gpus=ready_gpus,
        total_gpus=sum(sp.gpu_count for sp in specs),
        est_instances=ready_gpus * sellable_per_gpu(pool_label, gpu_cores_pct, oversell_cores),
        warnings=warnings,
    )
