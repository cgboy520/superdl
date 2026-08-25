"""管理端路由(租户管理/配额覆盖/账号注销,自 router.py 拆分)。"""

from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import mark_audited_read, set_audit_target
from app.core.db import DbSession
from app.core.errors import AppError
from app.core.logging import mask_phone_value
from app.core.money import as_amount
from app.core.pagination import Page
from app.core.params import TzOffset
from app.modules.account.schemas import AdminDeletionReject, AdminDeletionRequestOut
from app.modules.adminapi import service
from app.modules.adminapi.deps import CurrentAdmin, require_roles
from app.modules.adminapi.models import AdminUser
from app.modules.adminapi.router_shared import ExportLang, csv_response
from app.modules.adminapi.schemas import (
    REASON_MAX_LENGTH,
    AdjustContextOut,
    TenantOut,
    TenantQuotaOut,
    TenantQuotaUpdate,
    TenantStatusOut,
)
from app.modules.billing.schemas import (
    BillHourlyOut,
    LedgerEntryOut,
)
from app.modules.orchestrator import service as orchestrator_service

router = APIRouter(tags=["admin"])


# ---------- 租户管理(角色:admin / ops) ----------


class TenantFreezeRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=REASON_MAX_LENGTH)


@router.get("/tenants", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_list_tenants(
    session: DbSession,
    request: Request,
    admin: CurrentAdmin,
    q: str | None = None,
    status: str | None = None,
    cursor: str | None = None,
    limit: int | None = Query(default=None, le=100),
) -> Page[TenantOut]:
    """租户列表(游标分页,降序)。q = 手机号(完整号码精确,短串按后缀);纯数字额外按租户 id 精确命中。

    订单/调账/异常/实例全以 user_id 指代租户,运营常拿着 id 找人:id 命中行插在首页最前,
    手机号后缀命中行保持原序随后。手机号只回掩码。按号码/id 检索是敏感读,显式落一条审计
    (中间件默认只审计写操作)。

    实名信息:readonly 脱敏;其余角色明文 —— 响应里只要真含实名字段(有人已实名),
    本次明文读就落一条审计;全空实名或脱敏响应不记,避免列表页刷审计写放大。
    """
    from app.modules.account import service as account_service
    from app.modules.billing import service as billing_service

    if q:
        masked = mask_phone_value(q)
        mark_audited_read(request, f"tenant-search:{masked}", detail={"query_len": len(q)})
    page = await account_service.admin_list_users(
        session, q=q, status=status, cursor=cursor, limit=limit
    )
    users = list(page.items)
    q_digits = (q or "").strip()
    # 纯数字额外按租户 id 精确命中(仅首页注入,翻页不重复);id 是 int32,超过 9 位的数字串跳过
    if cursor is None and q_digits.isdigit() and len(q_digits) <= 9:
        by_id = None
        try:
            by_id = await account_service.get_user(session, int(q_digits))
        except AppError:
            by_id = None  # id 无命中,保留手机号后缀匹配结果
        if (
            by_id is not None
            and (not status or by_id.status == status)
            and all(u.id != by_id.id for u in users)
        ):
            users.insert(0, by_id)
    # 只聚合本页用户:三个按 user 分组的聚合都带 IN 过滤,不做全表 GROUP BY
    page_user_ids = [u.id for u in users]
    balances = await billing_service.balances_by_user(session, page_user_ids)
    consumed = await billing_service.consumed_by_user(session, page_user_ids)
    stats = await orchestrator_service.instance_disk_stats_by_user(session, page_user_ids)
    mask_realname = admin.role == "readonly"
    realname_hits = 0
    out = []
    for u in users:
        st = stats.get(u.id, {"instances": 0, "disk_gb": 0})
        verification_status, id_name = account_service.realname_view(u, masked=mask_realname)
        if not mask_realname and u.id_name:
            realname_hits += 1
        out.append(
            TenantOut(
                id=u.id,
                phone_masked=mask_phone_value(u.phone),
                status=u.status,
                balance=format(as_amount(balances.get(u.id, 0)), "f"),
                total_consumed=format(as_amount(consumed.get(u.id, 0)), "f"),
                instances=st["instances"],
                disk_gb=st["disk_gb"],
                created_at=u.created_at.isoformat(),
                verification_status=verification_status,
                id_name=id_name,
            )
        )
    if realname_hits:
        # 明文实名的敏感读留痕:target 只落条数不落内容(内容即 PII,审计里不再复制一份)
        mark_audited_read(request, "tenant-realname:list", detail={"rows": realname_hits})
    return Page[TenantOut](items=out, next_cursor=page.next_cursor)


@router.get("/tenants/{user_id}/ledger", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_tenant_ledger(
    user_id: int,
    session: DbSession,
    cursor: str | None = None,
    limit: int | None = Query(default=None, le=100),
) -> Page[LedgerEntryOut]:
    """租户资金流水下钻。与用户端同一实现,同一游标语义。"""
    from app.modules.billing import service as billing_service

    return await billing_service.ledger_page(session, user_id, cursor=cursor, limit=limit)


@router.get(
    "/tenants/{user_id}/ledger/export",
    dependencies=[require_roles("ops", "finance", "readonly")],
    responses={
        200: {"description": "CSV 导出", "content": {"text/csv": {"schema": {"type": "string"}}}}
    },
)
async def admin_tenant_ledger_export(
    user_id: int,
    session: DbSession,
    tz_offset_minutes: int = TzOffset,
    lang: Literal["zh-CN", "en-US"] = ExportLang,
) -> StreamingResponse:
    """租户资金流水 CSV(流式):与「流水」Tab 同一数据源,行数硬上限 + 截断标记行。"""
    from app.modules.billing import service as billing_service

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
    cursor: str | None = None,
    limit: int | None = Query(default=None, le=100),
) -> Page[BillHourlyOut]:
    """租户小时账单下钻(可按实例过滤;金额与用户端所见同源)。"""
    from app.modules.billing import service as billing_service

    return await billing_service.hourly_bills_page(
        session, user_id, instance_id=instance_id, cursor=cursor, limit=limit
    )


# ---------- 租户配额覆盖(读全角色,写 ops;校验链 override → policy → env) ----------


async def _tenant_quota_out(session: AsyncSession, user_id: int) -> TenantQuotaOut:
    from app.modules.account import service as account_service

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
    """配额覆盖现状 + 生效值(抽屉「配额」Tab 数据源)。"""
    return await _tenant_quota_out(session, user_id)


@router.put("/tenants/{user_id}/quota", dependencies=[require_roles("ops")])
async def admin_set_tenant_quota(
    user_id: int, body: TenantQuotaUpdate, session: DbSession, request: Request, admin: CurrentAdmin
) -> TenantQuotaOut:
    """写配额覆盖(三个数字可留空 = 该维走默认链;全空 = 清除覆盖)。note 必填,审计落前后值。"""
    from app.modules.account import service as account_service

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
    """调账前置上下文(只读):回显掩码手机号/当前余额/近 3 条流水。不存在 → 404。"""
    mark_audited_read(request, f"tenant-adjust-context:{user_id}")
    return AdjustContextOut.model_validate(await service.adjust_context(session, user_id))


@router.post("/tenants/{user_id}/freeze", dependencies=[require_roles("ops")])
async def admin_freeze_tenant(
    user_id: int, body: TenantFreezeRequest, session: DbSession, request: Request
) -> TenantStatusOut:
    from app.modules.account import service as account_service
    from app.modules.orchestrator import service as orchestrator_service

    user = await account_service.admin_set_user_status(session, user_id, "frozen")
    # 封禁同时停机:计费主链路不看用户状态,只改 status 会让被封账号的 GPU 继续跑并继续扣费。
    # 与 status 变更同一事务提交,K8s 动作走 outbox。
    stopped = await orchestrator_service.stop_all_for_user(session, user_id, reason="tenant_frozen")
    await session.commit()
    set_audit_target(
        request, f"user:{user_id}", detail={"reason": body.reason, "instances_stopped": stopped}
    )
    # 回显停机台数:前端据此提示「已停 N 台」(creating/starting 由巡检收敛,不在此计数)
    return TenantStatusOut(id=user.id, status=user.status, instances_stopped=stopped)


@router.post("/tenants/{user_id}/unfreeze", dependencies=[require_roles("ops")])
async def admin_unfreeze_tenant(
    user_id: int, body: TenantFreezeRequest, session: DbSession, request: Request
) -> TenantStatusOut:
    from app.modules.account import service as account_service
    from app.modules.notify import service as notify_service

    user = await account_service.admin_set_user_status(session, user_id, "active")
    # 刻意不自动开机:解封即批量拉起会立刻又欠费停机,由用户自行开机
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


# ---------- 账号注销(读 ops/finance/readonly,写仅 admin——最高危操作) ----------


@router.get("/deletion-requests", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_list_deletion_requests(
    session: DbSession, status: str | None = None
) -> list[AdminDeletionRequestOut]:
    """注销申请列表(固定截断 200)。行内附执行前校验计数(实例/盘/余额)。"""
    from app.modules.account import service as account_service

    return await account_service.admin_list_deletion_requests(session, status)


@router.post("/deletion-requests/{request_id}/approve")
async def admin_approve_deletion(
    request_id: int,
    session: DbSession,
    request: Request,
    admin: AdminUser = require_roles(),
) -> AdminDeletionRequestOut:
    """执行注销:冷静期未满 409;残留实例/数据盘或余额非零 → 自动驳回 + 409(detail 清单);
    全通过则同事务匿名化(手机号哈希化、实名清空、全撤登录态)。"""
    from app.modules.account import service as account_service

    req = await account_service.approve_deletion(session, request_id, admin_id=admin.id)
    set_audit_target(
        request,
        f"user:{req.user_id}",
        detail={"action": "account_deletion_approve", "request_id": req.id},
    )
    return await account_service.admin_get_deletion_out(session, req.id)


@router.post("/deletion-requests/{request_id}/reject")
async def admin_reject_deletion(
    request_id: int,
    body: AdminDeletionReject,
    session: DbSession,
    request: Request,
    admin: AdminUser = require_roles(),
) -> AdminDeletionRequestOut:
    """驳回注销申请(理由必填,不受冷静期限制);驳回后用户可重新申请。"""
    from app.modules.account import service as account_service

    req = await account_service.reject_deletion(
        session, request_id, admin_id=admin.id, note=body.note
    )
    set_audit_target(
        request,
        f"user:{req.user_id}",
        detail={"action": "account_deletion_reject", "request_id": req.id, "note": body.note},
    )
    return await account_service.admin_get_deletion_out(session, req.id)
