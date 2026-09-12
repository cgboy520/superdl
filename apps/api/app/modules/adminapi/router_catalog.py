"""管理端路由(SKU 与镜像/预热)。"""

from decimal import Decimal

from fastapi import APIRouter, Request

from app.core.audit import set_audit_target
from app.core.db import DbSession
from app.modules.adminapi import overview
from app.modules.adminapi.deps import require_roles
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


# ---------- SKU 管理(角色:admin / ops) ----------


@router.get("/skus", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_list_skus(session: DbSession) -> list[SkuAdminOut]:
    """SKU 列表,组装台账容量与占用列。"""
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
    """SKU 表单实时容量预览(纯台账)。gpu_model 留空 = CPU 规格预览。"""
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
    """改价/下架影响面(只读):当前活跃实例数/涉及用户数/占用卡数。"""
    return SkuImpactOut.model_validate(await overview.sku_impact(session, sku_id))


@router.post("/skus", dependencies=[require_roles("ops")], status_code=201)
async def admin_create_sku(body: SkuCreate, session: DbSession, request: Request) -> SkuAdminOut:
    sku = await catalog_service.admin_create_sku(session, body)
    # 记完整初始值,供后续改价对照
    set_audit_target(
        request,
        f"sku:{sku.id}",
        detail={"created": SkuAdminOut.model_validate(sku).model_dump(mode="json")},
    )
    return SkuAdminOut.model_validate(sku)


@router.patch("/skus/{sku_id}", dependencies=[require_roles("ops")])
async def admin_update_sku(
    sku_id: int, body: SkuUpdate, session: DbSession, request: Request, force: bool = False
) -> SkuAdminOut:
    sku, before = await catalog_service.admin_update_sku(session, sku_id, body, force=force)
    set_audit_target(
        request,
        f"sku:{sku.id}",
        detail={
            "before": before,  # 只记本次实际变更字段的旧值
            "after": body.model_dump(exclude_unset=True, exclude={"reason"}, mode="json"),
            "reason": body.reason,
        },
    )
    return SkuAdminOut.model_validate(sku)


# ---------- 镜像与预热(读:ops/readonly,写:ops,admin 恒许) ----------


class ImageDeleteRequest(ReasonBody):
    pass


@router.get("/images", dependencies=[require_roles("ops", "readonly")])
async def admin_list_images(session: DbSession) -> list[AdminImageOut]:
    """镜像目录 + 每镜像预热覆盖率(纯 DB 聚合,不调 K8s)。"""
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
    """删除目录条目(cache 行 CASCADE;运行中实例的 image_ref 快照不受影响)。reason 必填。"""
    img = await catalog_service.get_image(session, image_id)
    set_audit_target(
        request, f"image:{image_id}", detail={"image_ref": img.image_ref, "reason": body.reason}
    )
    await catalog_service.admin_delete_image(session, image_id)


@router.post("/images/{image_id}/prewarm", dependencies=[require_roles("ops")])
async def admin_prewarm_image(
    image_id: int, session: DbSession, request: Request
) -> PrewarmEnqueuedOut:
    """立即预热:非 cached 行置 pending 并同事务入队。"""
    enqueued = await catalog_service.admin_prewarm_image(session, image_id)
    set_audit_target(request, f"image:{image_id}", detail={"enqueued": enqueued})
    return PrewarmEnqueuedOut(enqueued=enqueued)


@router.get("/images/{image_id}/nodes", dependencies=[require_roles("ops", "readonly")])
async def admin_image_nodes(image_id: int, session: DbSession) -> list[ImageNodeCacheOut]:
    """每节点缓存明细(failed 行含 last_error)。"""
    rows = await catalog_service.image_node_rows(session, image_id)
    return [ImageNodeCacheOut.model_validate(r) for r in rows]
