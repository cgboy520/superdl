"""Admin routes (SKUs and images / prewarming)."""

from decimal import Decimal

from fastapi import APIRouter, Request

from app.core.audit import set_audit_target
from app.core.db import DbSession
from app.core.ratelimit import check_rate_limit
from app.modules.adminapi import overview
from app.modules.adminapi.deps import CurrentAdmin, require_roles
from app.modules.adminapi.schemas import ReasonBody
from app.modules.catalog import service as catalog_service
from app.modules.catalog.schemas import (
    AdminImageOut,
    CapacityPreviewOut,
    ImageCreate,
    ImageNodeCacheOut,
    ImageUpdate,
    PrewarmEnqueuedOut,
    SkuAdminOut,
    SkuCreate,
    SkuImpactOut,
    SkuUpdate,
)

router = APIRouter(tags=["admin"])


@router.get("/skus", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_list_skus(session: DbSession) -> list[SkuAdminOut]:
    """SKU list with inventory capacity and occupancy columns."""
    return await catalog_service.admin_skus_out(session)


@router.get("/skus/capacity-preview", dependencies=[require_roles("ops", "readonly")])
async def sku_capacity_preview(
    session: DbSession,
    pool_label: str,
    gpu_model: str = "",
    gpu_cores_pct: int = 100,
    oversell_cores: Decimal = Decimal("1.00"),
    vram_gb: int | None = None,
    vcpu: int | None = None,
    mem_gb: int | None = None,
) -> CapacityPreviewOut:
    """Live capacity preview of the SKU form (pure inventory). Empty gpu_model = CPU SKU preview."""
    return await catalog_service.capacity_preview(
        session,
        pool_label=pool_label,
        gpu_model=gpu_model,
        gpu_cores_pct=gpu_cores_pct,
        oversell_cores=oversell_cores,
        vram_gb=vram_gb,
        vcpu=vcpu,
        mem_gb=mem_gb,
    )


@router.get("/skus/{sku_id}/impact", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_sku_impact(sku_id: int, session: DbSession) -> SkuImpactOut:
    """Price-change / unlisting impact (read-only): active instances / affected users / occupied
    cards."""
    return SkuImpactOut.model_validate(await overview.sku_impact(session, sku_id))


PRICING_WRITE_MAX_PER_HOUR = 20


async def _throttle_pricing_writes(admin_id: int) -> None:
    """SKU create / update and policy writes share the per-admin 20-per-hour bucket."""
    await check_rate_limit(
        f"admin-pricing:{admin_id}", max_attempts=PRICING_WRITE_MAX_PER_HOUR, window_seconds=3600.0
    )


@router.post("/skus", dependencies=[require_roles()], status_code=201)
async def admin_create_sku(
    body: SkuCreate, session: DbSession, request: Request, admin: CurrentAdmin
) -> SkuAdminOut:
    """Create an SKU: admin only, 20 per admin per hour."""
    await _throttle_pricing_writes(admin.id)
    sku = await catalog_service.admin_create_sku(session, body)
    set_audit_target(
        request,
        f"sku:{sku.id}",
        detail={"created": SkuAdminOut.model_validate(sku).model_dump(mode="json")},
    )
    return SkuAdminOut.model_validate(sku)


@router.patch("/skus/{sku_id}", dependencies=[require_roles()])
async def admin_update_sku(
    sku_id: int,
    body: SkuUpdate,
    session: DbSession,
    request: Request,
    admin: CurrentAdmin,
    force: bool = False,
) -> SkuAdminOut:
    """Update an SKU: admin only, 20 per admin per hour; price-change alert rules in
    catalog.service."""
    await _throttle_pricing_writes(admin.id)
    sku, before = await catalog_service.admin_update_sku(session, sku_id, body, force=force)
    set_audit_target(
        request,
        f"sku:{sku.id}",
        detail={
            "before": before,
            "after": body.model_dump(exclude_unset=True, exclude={"reason"}, mode="json"),
            "reason": body.reason,
        },
    )
    return SkuAdminOut.model_validate(sku)


class ImageDeleteRequest(ReasonBody):
    pass


@router.get("/images", dependencies=[require_roles("ops", "readonly")])
async def admin_list_images(session: DbSession) -> list[AdminImageOut]:
    """Image catalog + prewarm coverage per image (pure DB aggregate, no K8s call)."""
    return await catalog_service.admin_list_images_out(session)


@router.post("/images", dependencies=[require_roles("ops")], status_code=201)
async def admin_create_image(
    body: ImageCreate, session: DbSession, request: Request
) -> AdminImageOut:
    img = await catalog_service.admin_create_image(session, body)
    set_audit_target(request, f"image:{img.id}", detail={"image_ref": img.image_ref})
    return await catalog_service.admin_image_out(session, img)


@router.patch("/images/{image_id}", dependencies=[require_roles("ops")])
async def admin_update_image(
    image_id: int, body: ImageUpdate, session: DbSession, request: Request
) -> AdminImageOut:
    img = await catalog_service.admin_update_image(session, image_id, body)
    set_audit_target(
        request, f"image:{img.id}", detail=body.model_dump(exclude_unset=True, mode="json")
    )
    return await catalog_service.admin_image_out(session, img)


@router.delete("/images/{image_id}", dependencies=[require_roles("ops")], status_code=204)
async def admin_delete_image(
    image_id: int, body: ImageDeleteRequest, session: DbSession, request: Request
) -> None:
    """Delete the catalog entry (cache rows CASCADE; the image_ref snapshot of running instances is
    unaffected). reason required."""
    img = await catalog_service.get_image(session, image_id)
    set_audit_target(
        request, f"image:{image_id}", detail={"image_ref": img.image_ref, "reason": body.reason}
    )
    await catalog_service.admin_delete_image(session, image_id)


@router.post("/images/{image_id}/prewarm", dependencies=[require_roles("ops")])
async def admin_prewarm_image(
    image_id: int, session: DbSession, request: Request
) -> PrewarmEnqueuedOut:
    """Prewarm now: non-cached rows set to pending and enqueued in the same transaction."""
    enqueued = await catalog_service.admin_prewarm_image(session, image_id)
    set_audit_target(request, f"image:{image_id}", detail={"enqueued": enqueued})
    return PrewarmEnqueuedOut(enqueued=enqueued)


@router.get("/images/{image_id}/nodes", dependencies=[require_roles("ops", "readonly")])
async def admin_image_nodes(image_id: int, session: DbSession) -> list[ImageNodeCacheOut]:
    """Per-node cache details (failed rows carry last_error)."""
    rows = await catalog_service.image_node_rows(session, image_id)
    return [ImageNodeCacheOut.model_validate(r) for r in rows]
