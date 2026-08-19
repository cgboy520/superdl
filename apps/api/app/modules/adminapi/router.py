from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.core.audit import set_audit_target
from app.core.db import DbSession
from app.modules.adminapi import service
from app.modules.adminapi.deps import CurrentAdmin, require_roles
from app.modules.adminapi.models import AdminUser
from app.modules.adminapi.schemas import AdminLoginRequest, AdminOut, AdminToken
from app.modules.catalog import service as catalog_service
from app.modules.catalog.schemas import SkuAdminOut, SkuCreate, SkuUpdate
from app.modules.metering import service as metering_service
from app.modules.orchestrator import service as orchestrator_service
from app.modules.orchestrator.schemas import (
    AdminForceStopRequest,
    AdminInstanceOut,
    InstanceOut,
)

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
) -> list[AdminInstanceOut]:
    instances = await orchestrator_service.admin_list_instances(
        session, status_filter=status, user_id=user_id
    )
    return [AdminInstanceOut.model_validate(i) for i in instances]


@router.post("/instances/{uuid}/force-stop", dependencies=[require_roles("ops")])
async def admin_force_stop(
    uuid: str, body: AdminForceStopRequest, session: DbSession, request: Request
) -> InstanceOut:
    """强制停止(原因必填)。"""
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


# ---------- 租户管理(角色:admin / ops) ----------


class TenantFreezeRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=256)


@router.get("/tenants", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_list_tenants(session: DbSession) -> list[dict]:
    from app.modules.account import service as account_service
    from app.modules.billing import service as billing_service

    users = await account_service.admin_list_users(session)
    balances = await billing_service.balances_by_user(session)
    consumed = await billing_service.consumed_by_user(session)
    stats = await orchestrator_service.instance_disk_stats_by_user(session)
    out = []
    for u in users:
        st = stats.get(u.id, {"instances": 0, "disk_gb": 0})
        out.append(
            {
                "id": u.id,
                "phone_masked": u.phone[:3] + "****" + u.phone[-4:],
                "status": u.status,
                "balance": format(balances.get(u.id, 0), "f"),
                "total_consumed": format(consumed.get(u.id, 0), "f"),
                "instances": st["instances"],
                "disk_gb": st["disk_gb"],
                "created_at": u.created_at.isoformat(),
            }
        )
    return out


@router.post("/tenants/{user_id}/freeze", dependencies=[require_roles("ops")])
async def admin_freeze_tenant(
    user_id: int, body: TenantFreezeRequest, session: DbSession, request: Request
) -> dict:
    from app.modules.account import service as account_service

    user = await account_service.admin_set_user_status(session, user_id, "frozen")
    set_audit_target(request, f"user:{user_id}", detail={"reason": body.reason})
    return {"id": user.id, "status": user.status}


@router.post("/tenants/{user_id}/unfreeze", dependencies=[require_roles("ops")])
async def admin_unfreeze_tenant(
    user_id: int, body: TenantFreezeRequest, session: DbSession, request: Request
) -> dict:
    from app.modules.account import service as account_service

    user = await account_service.admin_set_user_status(session, user_id, "active")
    set_audit_target(request, f"user:{user_id}", detail={"reason": body.reason})
    return {"id": user.id, "status": user.status}


# ---------- 节点与超卖报表(角色:admin / ops / readonly) ----------


@router.get("/nodes", dependencies=[require_roles("ops", "readonly")])
async def admin_list_nodes(session: DbSession) -> list[dict]:
    nodes = await orchestrator_service.cluster_nodes()
    return [
        {
            "name": n.name,
            "pool_label": n.pool_label,
            "gpu_model": n.gpu_model,
            "gpu_total": n.gpu_total,
            "gpu_used": n.gpu_used,
            "status": n.status,
        }
        for n in nodes
    ]


@router.get("/reports/oversell", dependencies=[require_roles("ops", "finance", "readonly")])
async def oversell_report(session: DbSession) -> list[dict]:
    """镇店报表:各池 物理容量 / 已售份额 / 实际超卖率 / 近 24h 真实利用率。"""
    from sqlalchemy import func as sa_func
    from sqlalchemy import select as sa_select

    from app.core.timeutil import now_utc
    from app.modules.metering.models import UsageHourly

    nodes = await orchestrator_service.cluster_nodes()
    physical: dict[str, int] = {}
    for n in nodes:
        physical[n.pool_label] = physical.get(n.pool_label, 0) + n.gpu_total
    sold = await orchestrator_service.running_gpu_share_by_pool(session)

    from datetime import timedelta

    since = now_utc() - timedelta(hours=24)
    util_avg = (
        await session.execute(
            sa_select(sa_func.avg(UsageHourly.gpu_util_avg)).where(
                UsageHourly.hour_start >= since, UsageHourly.gpu_util_avg.is_not(None)
            )
        )
    ).scalar_one()

    out = []
    for pool, total in sorted(physical.items()):
        sold_share = round(sold.get(pool, 0.0), 2)
        out.append(
            {
                "pool": pool,
                "physical_gpus": total,
                "sold_share": sold_share,
                "oversell_ratio": round(sold_share / total, 3) if total else 0.0,
                "util_avg_24h": round(float(util_avg), 1) if util_avg is not None else None,
            }
        )
    return out


# ---------- 调账(发起:finance;复核:另一名 finance/admin) ----------


class AdjustmentCreate(BaseModel):
    user_id: int
    amount: str  # 带符号金额字符串,如 "-10.00" / "25.50"
    reason: str = Field(min_length=2, max_length=256)


class AdjustmentReview(BaseModel):
    approve: bool
    comment: str | None = Field(default=None, max_length=256)


@router.get("/adjustments", dependencies=[require_roles("finance", "readonly")])
async def admin_list_adjustments(session: DbSession) -> list[dict]:
    rows = await service.list_adjustments(session)
    return [
        {
            "id": r.id,
            "user_id": r.user_id,
            "amount": format(r.amount, "f"),
            "reason": r.reason,
            "status": r.status,
            "created_by": r.created_by,
            "reviewed_by": r.reviewed_by,
            "review_comment": r.review_comment,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.post("/adjustments", status_code=201)
async def admin_create_adjustment(
    body: AdjustmentCreate,
    session: DbSession,
    request: Request,
    admin: AdminUser = require_roles("finance"),
) -> dict:
    adj = await service.create_adjustment(
        session,
        user_id=body.user_id,
        amount=body.amount,
        reason=body.reason,
        created_by=admin.id,
    )
    set_audit_target(request, f"adjustment:{adj.id}", detail={"amount": str(adj.amount)})
    return {"id": adj.id, "status": adj.status}


@router.post("/adjustments/{adjustment_id}/review")
async def admin_review_adjustment(
    adjustment_id: int,
    body: AdjustmentReview,
    session: DbSession,
    request: Request,
    admin: AdminUser = require_roles("finance"),
) -> dict:
    adj = await service.review_adjustment(
        session,
        adjustment_id,
        approve=body.approve,
        reviewer_id=admin.id,
        comment=body.comment,
    )
    set_audit_target(request, f"adjustment:{adj.id}", detail={"approve": body.approve})
    return {"id": adj.id, "status": adj.status}


# ---------- 审计检索(角色:admin) ----------


@router.get("/audit", dependencies=[require_roles("readonly")])
async def admin_audit_log(
    session: DbSession, actor_type: str | None = None, limit: int = 100
) -> list[dict]:
    from sqlalchemy import select as sa_select

    from app.core.audit import AuditLog

    stmt = sa_select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 500))
    if actor_type:
        stmt = stmt.where(AuditLog.actor_type == actor_type)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": r.id,
            "actor_type": r.actor_type,
            "actor_id": r.actor_id,
            "action": r.action,
            "target": r.target,
            "ip": str(r.ip) if r.ip else None,
            "result": r.result,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


# ---------- 财务流水(角色:finance) ----------


@router.get("/orders", dependencies=[require_roles("finance", "readonly")])
async def admin_list_orders(session: DbSession, status: str | None = None) -> list[dict]:
    from app.modules.billing import service as billing_service
    from app.modules.billing.schemas import RechargeOut

    rows = await billing_service.admin_list_orders(session, status)
    return [
        {**RechargeOut.model_validate(r).model_dump(mode="json"), "user_id": r.user_id}
        for r in rows
    ]
