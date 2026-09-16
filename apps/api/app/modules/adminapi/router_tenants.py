"""管理端路由(租户管理/配额覆盖/账号注销)。"""

from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import mark_audited_read, set_audit_target
from app.core.csvexport import CSV_RESPONSES, csv_response
from app.core.db import DbSession
from app.core.errors import AppError
from app.core.handles import mask_handle
from app.core.metrics import PII_REVEAL_ROWS_TOTAL
from app.core.money import as_amount, money_str
from app.core.pagination import Page
from app.core.params import Cursor, Limit, TzOffset
from app.modules.account import deletion as account_deletion, service as account_service
from app.modules.account.schemas import (
    AdminDeletionApprove,
    AdminDeletionReject,
    AdminDeletionRequestOut,
)
from app.modules.adminapi import auth_service, overview
from app.modules.adminapi.deps import CurrentAdmin, require_roles
from app.modules.adminapi.models import AdminUser
from app.modules.adminapi.router_shared import ExportLang
from app.modules.adminapi.schemas import (
    REASON_MAX_LENGTH,
    AdjustContextOut,
    ReasonBody,
    TenantOut,
    TenantQuotaOut,
    TenantQuotaUpdate,
    TenantStatusOut,
)
from app.modules.billing import service as billing_service
from app.modules.billing.schemas import (
    BillHourlyOut,
    LedgerEntryOut,
)
from app.modules.notify import service as notify_service
from app.modules.orchestrator import (
    queries as orchestrator_queries,
    transitions as orchestrator_transitions,
)

router = APIRouter(tags=["admin"])


class TenantFreezeRequest(ReasonBody):
    pass


@router.get("/tenants", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_list_tenants(
    session: DbSession,
    request: Request,
    admin: CurrentAdmin,
    q: str | None = None,
    status: str | None = None,
    cursor: str | None = Cursor,
    limit: int | None = Limit,
    order: Literal["asc", "desc"] = "desc",
    reveal: bool = False,
    reason: str | None = Query(default=None, max_length=REASON_MAX_LENGTH),
) -> Page[TenantOut]:
    """Tenants (cursor paged). q = email (exact when it contains `@`, prefix otherwise), E.164
    phone (exact with `+`, suffix for bare digits); bare digits also hit the tenant id exactly and
    are pinned first on the first page. Handles are masked; every search writes an audit row.
    order = id 正/倒序;聚合列按页拼装,不支持排序。
    实名信息默认脱敏;reveal=true 且 reason 必填回明文(readonly 不可),每次按条数+事由落审计。
    """
    reveal_reason = (
        auth_service.ensure_reveal_allowed(role=admin.role, reason=reason) if reveal else ""
    )
    if q:
        masked = mask_handle(q)
        mark_audited_read(request, f"tenant-search:{masked}", detail={"query_len": len(q)})
    page = await account_service.admin_list_users(
        session, q=q, status=status, cursor=cursor, limit=limit, order=order
    )
    users = list(page.items)
    q_digits = (q or "").strip()
    if cursor is None and q_digits.isdigit() and len(q_digits) <= 9:
        by_id = None
        try:
            by_id = await account_service.get_user(session, int(q_digits))
        except AppError:
            by_id = None
        if (
            by_id is not None
            and (not status or by_id.status == status)
            and all(u.id != by_id.id for u in users)
        ):
            users.insert(0, by_id)
    page_user_ids = [u.id for u in users]
    balances = await billing_service.balances_by_user(session, page_user_ids)
    consumed = await billing_service.consumed_by_user(session, page_user_ids)
    stats = await orchestrator_queries.instance_disk_stats_by_user(session, page_user_ids)
    mask_realname = not reveal
    realname_hits = 0
    out = []
    for u in users:
        st = stats.get(u.id, {"instances": 0, "disk_gb": 0})
        kyc_status, kyc_name = account_service.realname_view(u, masked=mask_realname)
        if not mask_realname and u.kyc_name:
            realname_hits += 1
        out.append(
            TenantOut(
                id=u.id,
                email_masked=mask_handle(u.email) if u.email else None,
                phone_masked=mask_handle(u.phone) if u.phone else None,
                status=u.status,
                balance=money_str(as_amount(balances.get(u.id, 0))),
                total_consumed=money_str(as_amount(consumed.get(u.id, 0))),
                instances=st["instances"],
                disk_gb=st["disk_gb"],
                created_at=u.created_at.isoformat(),
                kyc_status=kyc_status,
                kyc_name=kyc_name,
            )
        )
    if realname_hits:
        PII_REVEAL_ROWS_TOTAL.labels(kind="tenant_realname").inc(realname_hits)
        mark_audited_read(
            request,
            "tenant-realname:reveal",
            detail={"rows": realname_hits, "reason": reveal_reason},
        )
    return Page[TenantOut](items=out, next_cursor=page.next_cursor)


@router.get("/tenants/{user_id}/ledger", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_tenant_ledger(
    user_id: int,
    session: DbSession,
    cursor: str | None = Cursor,
    limit: int | None = Limit,
) -> Page[LedgerEntryOut]:
    """租户资金流水下钻。与用户端同一实现,同一游标语义。"""
    return await billing_service.ledger_page(session, user_id, cursor=cursor, limit=limit)


@router.get(
    "/tenants/{user_id}/ledger/export",
    dependencies=[require_roles("ops", "finance", "readonly")],
    responses=CSV_RESPONSES,
)
async def admin_tenant_ledger_export(
    user_id: int,
    session: DbSession,
    tz_offset_minutes: int = TzOffset,
    lang: Literal["zh-CN", "en-US"] = ExportLang,
) -> StreamingResponse:
    """租户资金流水 CSV(流式):与「流水」Tab 同一数据源,行数硬上限 + 截断标记行。"""
    return csv_response(
        billing_service.stream_ledger_csv(
            session, user_id, tz_offset_minutes=tz_offset_minutes, lang=lang
        ),
        f"superdl-tenant-{user_id}-ledger.csv",
    )


@router.get("/tenants/{user_id}/bills", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_tenant_bills(
    user_id: int,
    session: DbSession,
    instance_id: int | None = None,
    cursor: str | None = Cursor,
    limit: int | None = Limit,
) -> Page[BillHourlyOut]:
    """租户小时账单下钻(可按实例过滤)。"""
    return await billing_service.hourly_bills_page(
        session, user_id, instance_id=instance_id, cursor=cursor, limit=limit
    )


async def _tenant_quota_out(session: AsyncSession, user_id: int) -> TenantQuotaOut:
    override = await account_service.get_quota_override(session, user_id)
    limits = await account_service.get_user_limits(session, user_id)
    return TenantQuotaOut(
        user_id=user_id,
        max_gpus=override.max_gpus if override else None,
        max_instances=override.max_instances if override else None,
        max_disks=override.max_disks if override else None,
        effective_max_gpus=limits.max_gpus,
        effective_max_instances=limits.max_instances,
        effective_max_disks=limits.max_disks,
        note=override.note if override else None,
        updated_by=override.updated_by if override else None,
        updated_at=override.updated_at.isoformat() if override and override.updated_at else None,
    )


@router.get("/tenants/{user_id}/quota", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_get_tenant_quota(user_id: int, session: DbSession) -> TenantQuotaOut:
    """配额覆盖现状 + 生效值。"""
    return await _tenant_quota_out(session, user_id)


@router.put("/tenants/{user_id}/quota", dependencies=[require_roles("ops")])
async def admin_set_tenant_quota(
    user_id: int, body: TenantQuotaUpdate, session: DbSession, request: Request, admin: CurrentAdmin
) -> TenantQuotaOut:
    """写配额覆盖(数字留空 = 该维走默认链;全空 = 清除覆盖)。note 必填,审计落前后值。"""
    before = await account_service.get_quota_override(session, user_id)
    await account_service.set_quota_override(
        session,
        user_id,
        max_gpus=body.max_gpus,
        max_instances=body.max_instances,
        max_disks=body.max_disks,
        note=body.note,
        updated_by=admin.id,
    )
    await session.commit()
    set_audit_target(
        request,
        f"user:{user_id}",
        detail={
            "note": body.note,
            "before": (
                None
                if before is None
                else {
                    "max_gpus": before.max_gpus,
                    "max_instances": before.max_instances,
                    "max_disks": before.max_disks,
                }
            ),
            "after": {
                "max_gpus": body.max_gpus,
                "max_instances": body.max_instances,
                "max_disks": body.max_disks,
            },
        },
    )
    return await _tenant_quota_out(session, user_id)


@router.get(
    "/tenants/{user_id}/adjust-context", dependencies=[require_roles("ops", "finance", "readonly")]
)
async def admin_adjust_context(
    user_id: int, session: DbSession, request: Request
) -> AdjustContextOut:
    """调账前置上下文(只读):掩码手机号/当前余额/近 3 条流水。不存在 → 404。"""
    mark_audited_read(request, f"tenant-adjust-context:{user_id}")
    return AdjustContextOut.model_validate(await overview.adjust_context(session, user_id))


@router.post("/tenants/{user_id}/freeze", dependencies=[require_roles("ops")])
async def admin_freeze_tenant(
    user_id: int, body: TenantFreezeRequest, session: DbSession, request: Request
) -> TenantStatusOut:
    user = await account_service.admin_set_user_status(session, user_id, "frozen")
    stopped = await orchestrator_transitions.stop_all_for_user(
        session, user_id, reason="tenant_frozen"
    )
    await session.commit()
    set_audit_target(
        request, f"user:{user_id}", detail={"reason": body.reason, "instances_stopped": stopped}
    )
    return TenantStatusOut(id=user.id, status=user.status, instances_stopped=stopped)


@router.post("/tenants/{user_id}/unfreeze", dependencies=[require_roles("ops")])
async def admin_unfreeze_tenant(
    user_id: int, body: TenantFreezeRequest, session: DbSession, request: Request
) -> TenantStatusOut:
    user = await account_service.admin_set_user_status(session, user_id, "active")
    await notify_service.notify(
        session,
        user_id,
        type_="account",
        title="账号已恢复正常",
        content="您的账号已解除冻结。冻结期间被停止的实例需要您手动开机(实例盘数据保留)。",
        severity="info",
        dedup_key=f"unfrozen:{user_id}",
    )
    await session.commit()
    set_audit_target(request, f"user:{user_id}", detail={"reason": body.reason})
    return TenantStatusOut(id=user.id, status=user.status)


@router.get("/deletion-requests", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_list_deletion_requests(
    session: DbSession, status: str | None = None
) -> list[AdminDeletionRequestOut]:
    """注销申请列表(固定截断 200),行内附执行前校验计数。"""
    return await account_deletion.admin_list_deletion_requests(session, status)


@router.post("/deletion-requests/{request_id}/approve")
async def admin_approve_deletion(
    request_id: int,
    body: AdminDeletionApprove,
    session: DbSession,
    request: Request,
    admin: AdminUser = require_roles(),
) -> AdminDeletionRequestOut:
    """执行注销(操作原因必填):冷静期未满 409;残留实例/数据盘或余额非零 → 自动驳回 + 409
    (detail 清单);全通过则同事务匿名化并把原因回写 note。"""
    req = await account_deletion.approve_deletion(
        session, request_id, admin_id=admin.id, note=body.note
    )
    set_audit_target(
        request,
        f"user:{req.user_id}",
        detail={"action": "account_deletion_approve", "request_id": req.id, "note": body.note},
    )
    return await account_deletion.admin_get_deletion_out(session, req.id)


@router.post("/deletion-requests/{request_id}/reject")
async def admin_reject_deletion(
    request_id: int,
    body: AdminDeletionReject,
    session: DbSession,
    request: Request,
    admin: AdminUser = require_roles(),
) -> AdminDeletionRequestOut:
    """驳回注销申请(理由必填,不受冷静期限制)。"""
    req = await account_deletion.reject_deletion(
        session, request_id, admin_id=admin.id, note=body.note
    )
    set_audit_target(
        request,
        f"user:{req.user_id}",
        detail={"action": "account_deletion_reject", "request_id": req.id, "note": body.note},
    )
    return await account_deletion.admin_get_deletion_out(session, req.id)
