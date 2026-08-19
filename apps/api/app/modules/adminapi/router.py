from fastapi import APIRouter, Request

from app.core.audit import set_audit_target
from app.core.db import DbSession
from app.modules.adminapi import service
from app.modules.adminapi.deps import CurrentAdmin, require_roles
from app.modules.adminapi.schemas import AdminLoginRequest, AdminOut, AdminToken
from app.modules.catalog import service as catalog_service
from app.modules.catalog.schemas import SkuAdminOut, SkuCreate, SkuUpdate
from app.modules.metering import service as metering_service
from app.modules.orchestrator import service as orchestrator_service
from app.modules.orchestrator.schemas import AdminForceStopRequest, InstanceOut

router = APIRouter(tags=["admin"])


@router.post("/auth/login")
async def admin_login(body: AdminLoginRequest, session: DbSession, request: Request) -> AdminToken:
    client_ip = request.client.host if request.client else None
    token, admin = await service.login(session, body.username, body.password, client_ip=client_ip)
    set_audit_target(request, f"admin:{admin.id}")
    return AdminToken(access_token=token, admin=AdminOut.model_validate(admin))


@router.get("/me")
async def admin_me(admin: CurrentAdmin) -> AdminOut:
    return AdminOut.model_validate(admin)


# ---------- SKU 管理(角色:admin / ops) ----------


@router.get("/skus", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_list_skus(session: DbSession) -> list[SkuAdminOut]:
    skus = await catalog_service.admin_list_skus(session)
    return [SkuAdminOut.model_validate(s) for s in skus]


@router.post("/skus", dependencies=[require_roles("ops")], status_code=201)
async def admin_create_sku(body: SkuCreate, session: DbSession, request: Request) -> SkuAdminOut:
    sku = await catalog_service.admin_create_sku(session, body)
    set_audit_target(request, f"sku:{sku.id}", detail={"name": sku.name})
    return SkuAdminOut.model_validate(sku)


@router.patch("/skus/{sku_id}", dependencies=[require_roles("ops")])
async def admin_update_sku(
    sku_id: int, body: SkuUpdate, session: DbSession, request: Request
) -> SkuAdminOut:
    sku = await catalog_service.admin_update_sku(session, sku_id, body)
    set_audit_target(
        request, f"sku:{sku.id}", detail=body.model_dump(exclude_unset=True, mode="json")
    )
    return SkuAdminOut.model_validate(sku)


# ---------- 全局实例(角色:admin / ops) ----------


@router.get("/instances", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_list_instances(
    session: DbSession, status: str | None = None, user_id: int | None = None
) -> list[InstanceOut]:
    instances = await orchestrator_service.admin_list_instances(
        session, status_filter=status, user_id=user_id
    )
    return [InstanceOut.model_validate(i) for i in instances]


@router.post("/instances/{uuid}/force-stop", dependencies=[require_roles("ops")])
async def admin_force_stop(
    uuid: str, body: AdminForceStopRequest, session: DbSession, request: Request
) -> InstanceOut:
    """强制停止(原因必填,通知用户由 WP9 接入)。"""
    instance = await orchestrator_service.admin_force_stop(session, uuid, reason=body.reason)
    set_audit_target(request, f"instance:{uuid}", detail={"reason": body.reason})
    return InstanceOut.model_validate(instance)


# ---------- 财务对账(角色:finance / admin) ----------


@router.get("/reconciliation", dependencies=[require_roles("finance", "readonly")])
async def reconciliation(session: DbSession, day: str) -> dict:
    """日对账:事件计费 vs 指标估算 + diff%(>2% 列差异实例)。"""
    from datetime import UTC, datetime

    from app.core.errors import AppError, ErrorCode

    try:
        d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, "day 格式应为 YYYY-MM-DD") from exc
    return await metering_service.reconciliation_report(session, d)


@router.get("/alerts", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_alerts(session: DbSession) -> list[dict]:
    """管理端告警流(总览右栏数据源)。"""
    from app.modules.notify import service as notify_service

    rows = await notify_service.admin_alert_stream(session)
    return [
        {
            "id": r.id,
            "type": r.type,
            "title": r.title,
            "content": r.content,
            "severity": r.severity,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
