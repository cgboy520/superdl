"""管理端路由(SKU 与镜像/预热,自 router.py 拆分)。"""

from decimal import ROUND_HALF_EVEN, Decimal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.core.audit import set_audit_target
from app.core.db import DbSession
from app.core.gpu_models import canonical_gpu_model
from app.modules.adminapi import service
from app.modules.adminapi.deps import require_roles
from app.modules.adminapi.schemas import (
    AdminImageOut,
    CapacityPreviewOut,
    CapacityWarningOut,
    ImageCoverageOut,
    ImageNodeCacheOut,
    PrewarmEnqueuedOut,
    SkuImpactOut,
)
from app.modules.catalog import service as catalog_service
from app.modules.catalog.schemas import (
    ImageCreate,
    ImageUpdate,
    SkuAdminOut,
    SkuCreate,
    SkuUpdate,
)
from app.modules.nodes import service as nodes_service
from app.modules.orchestrator import service as orchestrator_service

router = APIRouter(tags=["admin"])


# ---------- SKU 管理(角色:admin / ops) ----------


@router.get("/skus", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_list_skus(session: DbSession) -> list[SkuAdminOut]:
    """SKU 列表,组装台账容量与占用列(catalog+nodes+orchestrator 三 service 汇合点)。"""
    skus = await catalog_service.admin_list_skus(session)
    specs = await nodes_service.ready_specs(session)
    sold = await orchestrator_service.active_gpu_counts_by_sku(session)
    out: list[SkuAdminOut] = []
    for sku in skus:
        item = SkuAdminOut.model_validate(sku)
        wanted = canonical_gpu_model(sku.gpu_model)
        item.capacity_gpus = sum(
            sp.gpu_count for sp in nodes_service.matching_specs(specs, sku.pool_label, wanted)
        )
        if item.capacity_gpus:
            # 已售名义算力(卡×pct/100)对物理与对可售(×超卖)的两个比值,2 位小数
            nominal = Decimal(sold.get(sku.id, 0)) * Decimal(sku.gpu_cores_pct) / Decimal(100)
            cap = Decimal(item.capacity_gpus)
            item.actual_oversell = str(
                (nominal / cap).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
            )
            item.sold_share = str(
                (nominal / (cap * sku.oversell_cores)).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_EVEN
                )
            )
        out.append(item)
    return out


@router.get("/skus/capacity-preview", dependencies=[require_roles("ops", "readonly")])
async def sku_capacity_preview(
    session: DbSession,
    gpu_model: str,
    pool_label: str,
    tier: str,
    gpu_cores_pct: int = 100,
    oversell_cores: Decimal = Decimal("1.00"),
    vram_gb: int | None = None,
) -> CapacityPreviewOut:
    """SKU 表单实时容量预览(纯台账;创建仍软校验,上架才硬校验)。"""
    warnings: list[CapacityWarningOut] = []
    wanted = canonical_gpu_model(gpu_model)
    if wanted is None:
        warnings.append(CapacityWarningOut(code="unrecognized_model", params={"model": gpu_model}))
    specs = nodes_service.matching_specs(
        await nodes_service.list_node_specs(session), pool_label, wanted
    )
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
        est_instances=ready_gpus
        * catalog_service.sellable_per_gpu(tier, gpu_cores_pct, oversell_cores),
        warnings=warnings,
    )


@router.get("/skus/{sku_id}/impact", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_sku_impact(sku_id: int, session: DbSession) -> SkuImpactOut:
    """改价/下架影响面(只读):当前活跃实例数/涉及用户数/占用卡数。"""
    return SkuImpactOut.model_validate(await service.sku_impact(session, sku_id))


@router.post("/skus", dependencies=[require_roles("ops")], status_code=201)
async def admin_create_sku(body: SkuCreate, session: DbSession, request: Request) -> SkuAdminOut:
    sku = await catalog_service.admin_create_sku(session, body)
    # 记完整初始值(尤其单价),供后续改价对照
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


class ImageDeleteRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=256)


def _admin_image_out(img, coverage: dict[int, tuple[int, int, int]]) -> AdminImageOut:
    cached, total, failed = coverage.get(img.id, (0, 0, 0))
    return AdminImageOut(
        id=img.id,
        framework=img.framework,
        framework_version=img.framework_version,
        python_version=img.python_version,
        cuda_version=img.cuda_version,
        image_ref=img.image_ref,
        prewarm_enabled=img.prewarm_enabled,
        sort=img.sort,
        coverage=ImageCoverageOut(
            cached=cached, total=total, pct=(cached * 100 // total) if total else 0
        ),
        failed_nodes=failed,
    )


@router.get("/images", dependencies=[require_roles("ops", "readonly")])
async def admin_list_images(session: DbSession) -> list[AdminImageOut]:
    """镜像目录 + 每镜像预热覆盖率(纯 DB 聚合,不调 K8s)。"""
    images = await catalog_service.list_images(session)
    coverage = await catalog_service.image_coverage(session)
    return [_admin_image_out(img, coverage) for img in images]


@router.post("/images", dependencies=[require_roles("ops")], status_code=201)
async def admin_create_image(
    body: ImageCreate, session: DbSession, request: Request
) -> AdminImageOut:
    img = await catalog_service.admin_create_image(session, body)
    set_audit_target(request, f"image:{img.id}", detail={"image_ref": img.image_ref})
    return _admin_image_out(img, {})


@router.patch("/images/{image_id}", dependencies=[require_roles("ops")])
async def admin_update_image(
    image_id: int, body: ImageUpdate, session: DbSession, request: Request
) -> AdminImageOut:
    img = await catalog_service.admin_update_image(session, image_id, body)
    set_audit_target(
        request, f"image:{img.id}", detail=body.model_dump(exclude_unset=True, mode="json")
    )
    coverage = await catalog_service.image_coverage(session)
    return _admin_image_out(img, coverage)


@router.delete("/images/{image_id}", dependencies=[require_roles("ops")], status_code=204)
async def admin_delete_image(
    image_id: int, body: ImageDeleteRequest, session: DbSession, request: Request
) -> None:
    """删除目录条目(cache 行 CASCADE;运行中实例存 image_ref 快照不受影响)。reason 必填。"""
    img = await catalog_service.get_image(session, image_id)
    set_audit_target(
        request, f"image:{image_id}", detail={"image_ref": img.image_ref, "reason": body.reason}
    )
    await catalog_service.admin_delete_image(session, image_id)


@router.post("/images/{image_id}/prewarm", dependencies=[require_roles("ops")])
async def admin_prewarm_image(
    image_id: int, session: DbSession, request: Request
) -> PrewarmEnqueuedOut:
    """立即预热:非 cached 行置 pending 并同事务入队(请求路径零 K8s 调用)。"""
    enqueued = await catalog_service.admin_prewarm_image(session, image_id)
    set_audit_target(request, f"image:{image_id}", detail={"enqueued": enqueued})
    return PrewarmEnqueuedOut(enqueued=enqueued)


@router.get("/images/{image_id}/nodes", dependencies=[require_roles("ops", "readonly")])
async def admin_image_nodes(image_id: int, session: DbSession) -> list[ImageNodeCacheOut]:
    """每节点缓存明细(failed 行含 last_error)。"""
    rows = await catalog_service.image_node_rows(session, image_id)
    return [ImageNodeCacheOut.model_validate(r) for r in rows]
