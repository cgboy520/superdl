"""管理端路由(总览/工单/审计/策略/平台配置/公告/outbox 死信,自 router.py 拆分)。"""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.audit import mark_audited_read, set_audit_target
from app.core.db import DbSession
from app.core.errors import AppError, ErrorCode
from app.core.http import mark_idempotent_replay
from app.core.pagination import Page
from app.core.params import TzOffset
from app.core.platform_config import get_effective_platform_config
from app.core.sqlutil import like_escape
from app.modules.adminapi import export as admin_export
from app.modules.adminapi import service
from app.modules.adminapi.deps import CurrentAdmin, require_roles
from app.modules.adminapi.models import AdminUser
from app.modules.adminapi.router_shared import ExportLang, csv_response
from app.modules.adminapi.schemas import (
    AnnouncementOut,
    AnnouncementResultOut,
    AuditLogOut,
    DeadTaskOut,
    OutboxTaskOut,
    OutboxTaskStatusOut,
    OverviewOut,
    PlatformConfigItemOut,
    PlatformConfigOut,
    PoliciesAdminOut,
    SmsTestOut,
    UpdatedKeysOut,
)
from app.modules.tickets.schemas import (
    AdminTicketDetailOut,
    AdminTicketOut,
    AdminTicketReply,
    AdminTicketStatusUpdate,
)

router = APIRouter(tags=["admin"])


# ---------- 总览聚合(只读,全角色) ----------


@router.get("/overview", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_overview(session: DbSession) -> OverviewOut:
    """值班首屏聚合:实例分状态 COUNT、付费租户 COUNT、池级 GPU(含非 Ready)台账。

    全是精确计数,替代前端在截断列表(200/500 条)里数数的错误口径。
    """
    return OverviewOut.model_validate(await service.overview(session))


# ---------- 工单(读 ops/finance/readonly,写 ops/admin) ----------


@router.get("/tickets", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_list_tickets(
    session: DbSession,
    status: str | None = None,
    category: str | None = None,
    user_id: int | None = None,
    ticket_no: str | None = None,
    cursor: str | None = None,
    limit: int | None = Query(default=None, le=100),
) -> Page[AdminTicketOut]:
    """工单列表(游标分页,降序):status/category 精确过滤,user_id/ticket_no 检索。"""
    from app.modules.tickets import service as tickets_service

    return await tickets_service.admin_list_tickets(
        session,
        status=status,
        category=category,
        user_id=user_id,
        ticket_no=ticket_no,
        cursor=cursor,
        limit=limit,
    )


@router.get("/tickets/{ticket_id}", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_get_ticket(ticket_id: int, session: DbSession) -> AdminTicketDetailOut:
    """工单详情 + 消息流(时间升序)。"""
    from app.modules.tickets import service as tickets_service

    return await tickets_service.admin_get_ticket(session, ticket_id)


@router.post("/tickets/{ticket_id}/reply")
async def admin_reply_ticket(
    ticket_id: int,
    body: AdminTicketReply,
    session: DbSession,
    request: Request,
    admin: AdminUser = require_roles("ops"),
) -> AdminTicketDetailOut:
    """客服回复(→ pending_user),站内信告知用户;resolved/closed 不可再回复。"""
    from app.modules.tickets import service as tickets_service

    await tickets_service.admin_reply(session, ticket_id, body=body.body, operator_id=admin.id)
    set_audit_target(request, f"ticket:{ticket_id}", detail={"action": "reply"})
    return await tickets_service.admin_get_ticket(session, ticket_id)


@router.post("/tickets/{ticket_id}/status")
async def admin_update_ticket_status(
    ticket_id: int,
    body: AdminTicketStatusUpdate,
    session: DbSession,
    request: Request,
    admin: AdminUser = require_roles("ops"),
) -> AdminTicketOut:
    """标记解决/关闭(close 仅 resolved 后可;closed_at 仅 closed 落)。"""
    from app.modules.tickets import service as tickets_service

    ticket = await tickets_service.admin_update_status(session, ticket_id, action=body.action)
    set_audit_target(request, f"ticket:{ticket_id}", detail={"action": body.action, "by": admin.id})
    return AdminTicketOut.model_validate(ticket)


# ---------- 审计检索(所有已认证管理角色可读) ----------


@router.get("/audit", dependencies=[require_roles("readonly", "ops", "finance")])
async def admin_audit_log(
    session: DbSession,
    actor_type: str | None = None,
    actor_id: str | None = None,
    q: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    cursor: str | None = None,
) -> list[AuditLogOut]:
    """审计检索:actor_id / 动作前缀 / 时间区间;cursor 向前翻页(响应保持数组,满页即还有更早)。"""
    from sqlalchemy import select as sa_select

    from app.core.audit import AuditLog
    from app.core.pagination import decode_cursor_int

    stmt = sa_select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if actor_type:
        stmt = stmt.where(AuditLog.actor_type == actor_type)
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if q:
        # 动作/目标关键字。两列都是短串,量级由 limit 兜住;
        # LIKE 元字符转义:q 里的 %/_ 按字面匹配,不当通配符
        pattern = f"%{like_escape(q)}%"
        stmt = stmt.where(
            AuditLog.action.ilike(pattern, escape="\\")
            | AuditLog.target.ilike(pattern, escape="\\")
        )
    if since:
        stmt = stmt.where(AuditLog.created_at >= since)
    if until:
        stmt = stmt.where(AuditLog.created_at < until)
    last_id = decode_cursor_int(cursor)
    if last_id is not None:
        stmt = stmt.where(AuditLog.id < last_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        AuditLogOut(
            id=r.id,
            actor_type=r.actor_type,
            actor_id=r.actor_id,
            action=r.action,
            target=r.target,
            ip=str(r.ip) if r.ip else None,
            result=r.result,
            detail=r.detail,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]


@router.get(
    "/audit/export",
    dependencies=[require_roles("readonly", "ops", "finance")],
    responses={
        200: {"description": "CSV 导出", "content": {"text/csv": {"schema": {"type": "string"}}}}
    },
)
async def admin_audit_export(
    session: DbSession,
    request: Request,
    actor_type: str | None = None,
    actor_id: str | None = None,
    q: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    tz_offset_minutes: int = TzOffset,
    lang: Literal["zh-CN", "en-US"] = ExportLang,
) -> StreamingResponse:
    """审计检索 CSV(流式):筛选口径与 GET /audit 一致;行数硬上限 + 截断标记行。
    审计本身的批量导出是敏感读,落一条检索审计(只记筛选参数,不复制内容)。"""
    mark_audited_read(
        request,
        "audit:export",
        detail={"actor_type": actor_type, "actor_id": actor_id, "q": bool(q)},
    )
    return csv_response(
        admin_export.stream_audit_csv(
            session,
            actor_type=actor_type,
            actor_id=actor_id,
            q=q,
            since=since,
            until=until,
            tz_offset_minutes=tz_offset_minutes,
            lang=lang,
        ),
        "superdl-audit.csv",
    )


# ---------- 系统设置:策略参数在线调整(角色:ops) ----------


@router.get("/policies", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_get_policies(session: DbSession) -> PoliciesAdminOut:
    """当前生效策略 + 取值范围(供设置屏渲染)+ DB 覆盖项。"""
    from dataclasses import asdict

    from app.core.policies import POLICY_SPECS, get_effective_policies, list_policy_overrides

    effective = await get_effective_policies(session)
    return PoliciesAdminOut.model_validate(
        {
            "effective": {k: str(v) for k, v in asdict(effective).items()},
            "overrides": await list_policy_overrides(session),
            "specs": {
                k: {"kind": v[0], "min": str(v[1]), "max": str(v[2])}
                for k, v in POLICY_SPECS.items()
            },
        }
    )


class PolicyUpdateRequest(BaseModel):
    updates: dict[str, str] = Field(min_length=1)
    reason: str = Field(min_length=2, max_length=200)


@router.put("/policies", dependencies=[require_roles("ops")])
async def admin_update_policies(
    body: PolicyUpdateRequest, session: DbSession, request: Request
) -> UpdatedKeysOut:
    """在线调整策略参数(即时生效,GET /policies 与计费/回收同步跟随)。
    审计 detail 记变更前后值与原因(改策略与改价同级,须答得出「从多少改成多少」)。"""
    from dataclasses import asdict

    from app.core.policies import get_effective_policies, set_policy_overrides

    effective = await get_effective_policies(session)
    before_all = {k: str(v) for k, v in asdict(effective).items()}
    try:
        await set_policy_overrides(session, body.updates)
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, str(exc)) from exc
    await session.commit()
    set_audit_target(
        request,
        "policies",
        detail={
            "before": {k: before_all.get(k) for k in body.updates},
            "after": body.updates,
            "reason": body.reason,
        },
    )
    return UpdatedKeysOut(updated=sorted(body.updates))


# ---------- 平台配置:支付/短信/实名/合规(角色:仅 admin —— 渠道凭据不下放 ops) ----------


@router.get("/platform-config", dependencies=[require_roles()])
async def admin_get_platform_config(session: DbSession) -> PlatformConfigOut:
    """分组配置项:生效值 + 来源(env 默认/DB 覆盖)。secret 永不回明文,只回尾 4 位预览。"""
    from app.core.platform_config import (
        SETTING_SPECS,
        list_platform_overrides,
        secret_preview,
    )

    eff = await get_effective_platform_config(session)
    overrides = await list_platform_overrides(session)
    items = []
    for key, spec in SETTING_SPECS.items():
        value = eff[key]
        row = overrides.get(key)
        items.append(
            PlatformConfigItemOut(
                key=key,
                group=spec.group,
                kind=spec.kind,
                choices=list(spec.choices),
                hint=spec.hint,
                source="override" if row is not None else ("env" if value else "unset"),
                configured=bool(value),
                value=None if spec.kind == "secret" else value,
                preview=secret_preview(value) if spec.kind == "secret" and value else None,
                updated_at=row.updated_at.isoformat() if row is not None else None,
            )
        )
    return PlatformConfigOut(items=items)


class PlatformConfigUpdateRequest(BaseModel):
    updates: dict[str, str] = Field(min_length=1)
    reason: str = Field(min_length=2, max_length=200)


@router.put("/platform-config")
async def admin_update_platform_config(
    body: PlatformConfigUpdateRequest,
    session: DbSession,
    request: Request,
    admin: AdminUser = require_roles(),
) -> UpdatedKeysOut:
    """在线配置渠道凭据与合规信息(空串=清除覆盖,回退 env 默认)。审计只落键名不落值。"""
    from app.core.platform_config import set_platform_settings

    try:
        await set_platform_settings(session, body.updates, updated_by=admin.id)
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, str(exc)) from exc
    await session.commit()
    set_audit_target(
        request, "platform_config", detail={"keys": sorted(body.updates), "reason": body.reason}
    )
    return UpdatedKeysOut(updated=sorted(body.updates))


class SmsTestRequest(BaseModel):
    phone: str = Field(pattern=r"^1\d{10}$")


@router.post("/platform-config/test-sms", dependencies=[require_roles()])
async def admin_test_sms(body: SmsTestRequest, session: DbSession, request: Request) -> SmsTestOut:
    """按当前生效短信配置实发一条验证码短信(有限流,过审计)。"""
    import secrets

    from app.core.platform_config import get_effective_platform_config
    from app.core.ratelimit import check_rate_limit
    from app.core.sms import SmsError, ensure_sms_platform_quota, get_sms_channel

    await check_rate_limit("admin:test-sms", max_attempts=10, window_seconds=3600.0)
    await ensure_sms_platform_quota()  # 实发同样消耗平台预算池,与其他发送点同一闸门
    cfg = await get_effective_platform_config(session)
    channel = await get_sms_channel(session)
    code = f"{secrets.randbelow(10**6):06d}"
    try:
        await channel.send(body.phone, cfg["sms_template_verify"] or "", {"code": code})
    except SmsError as exc:
        raise AppError(
            ErrorCode.SMS_SEND_FAILED,
            key="adminapi.smsTestFailed",
            params={"message": str(exc)},
            http_status=502,
        ) from exc
    set_audit_target(request, f"test-sms:{body.phone}")
    return SmsTestOut(ok=True, provider=cfg["sms_provider"])


# ---------- 公告(角色:读 ops/finance/readonly,写 ops) ----------


class AnnouncementCreate(BaseModel):
    title: str = Field(min_length=2, max_length=128)
    content: str = Field(min_length=2, max_length=2000)


class AnnouncementRevoke(BaseModel):
    reason: str = Field(min_length=2, max_length=256)


def _announcement_out(a: Any) -> AnnouncementOut:
    return AnnouncementOut(
        id=a.id,
        title=a.title,
        content=a.content,
        status=a.status,
        reached=a.reached,
        created_by=a.created_by,
        created_at=a.created_at.isoformat(),
        revoked_by=a.revoked_by,
        revoked_at=a.revoked_at.isoformat() if a.revoked_at else None,
        revoke_reason=a.revoke_reason,
    )


@router.post("/announcements", dependencies=[require_roles("ops")], status_code=201)
async def admin_publish_announcement(
    body: AnnouncementCreate,
    admin: CurrentAdmin,
    session: DbSession,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AnnouncementResultOut:
    """公告群发(站内信 announcement 类型,全部 active 用户);落公告级记录供历史/撤回。
    Idempotency-Key 重放不新建公告(否则全员收到重复站内信),回 200 + X-Idempotent-Replay。"""
    from app.modules.notify import service as notify_service

    reached, created = await notify_service.publish_announcement(
        session,
        title=body.title,
        content=body.content,
        created_by=admin.id,
        idempotency_key=idempotency_key,
    )
    if not created:
        mark_idempotent_replay(response)
    set_audit_target(request, "announcement", detail={"title": body.title, "reached": reached})
    return AnnouncementResultOut(reached=reached)


@router.get("/announcements", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_list_announcements(session: DbSession) -> list[AnnouncementOut]:
    """公告历史(含已撤回;固定截断 200,前端 ListCapNote 提示)。"""
    from app.modules.notify import service as notify_service

    return [_announcement_out(a) for a in await notify_service.admin_list_announcements(session)]


@router.post("/announcements/{announcement_id}/revoke", dependencies=[require_roles("ops")])
async def admin_revoke_announcement(
    announcement_id: int,
    body: AnnouncementRevoke,
    admin: CurrentAdmin,
    session: DbSession,
    request: Request,
) -> AnnouncementOut:
    """撤回公告(原因必填,入审计):撤回后全部租户的站内信公告不再可见。重复撤回 409。"""
    from app.modules.notify import service as notify_service

    announcement = await notify_service.revoke_announcement(
        session, announcement_id, revoked_by=admin.id, reason=body.reason
    )
    set_audit_target(request, f"announcement:{announcement_id}", detail={"reason": body.reason})
    return _announcement_out(announcement)


# ---------- outbox 死信(角色:ops) ----------


@router.get("/outbox/tasks", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_list_outbox_tasks(
    session: DbSession,
    status: str | None = None,
    instance_id: int | None = None,
) -> list[OutboxTaskOut]:
    """outbox 全量查询(排障):不限死信(死信专用视图仍是 /outbox/dead)。

    status 精确过滤;instance_id 匹配 payload.instance_id(编排类任务都带)。
    固定截断 200(前端 ListCapNote 提示),按 id 倒序。
    """
    from sqlalchemy import select as sa_select

    from app.core.outbox import OutboxTask

    stmt = sa_select(OutboxTask).order_by(OutboxTask.id.desc()).limit(200)
    if status:
        stmt = stmt.where(OutboxTask.status == status)
    if instance_id is not None:
        # JSONB 文本比较:无该键的行得到 NULL 自然排除;数值与字符串两种形态都按文本命中
        stmt = stmt.where(OutboxTask.payload["instance_id"].astext == str(instance_id))
    rows = (await session.execute(stmt)).scalars().all()
    return [
        OutboxTaskOut(
            id=r.id,
            type=r.type,
            status=r.status,
            payload=r.payload,
            retries=r.retries,
            last_error=r.last_error,
            created_at=r.created_at.isoformat(),
            updated_at=r.updated_at.isoformat(),
        )
        for r in rows
    ]


@router.get("/outbox/dead", dependencies=[require_roles("ops", "readonly")])
async def admin_list_dead_tasks(session: DbSession) -> list[DeadTaskOut]:
    """死信任务列表:重试耗尽的编排任务在此可见(另有 outbox_dead_total 指标接告警)。"""
    from sqlalchemy import select as sa_select

    from app.core.outbox import OutboxTask

    rows = (
        (
            await session.execute(
                sa_select(OutboxTask)
                .where(OutboxTask.status == "dead")
                .order_by(OutboxTask.id.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return [
        DeadTaskOut(
            id=r.id,
            type=r.type,
            payload=r.payload,
            retries=r.retries,
            last_error=r.last_error,
            created_at=r.created_at.isoformat(),
            updated_at=r.updated_at.isoformat(),
        )
        for r in rows
    ]


class OutboxDiscardRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=200)


class OutboxRetryRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=200)


@router.post("/outbox/{task_id}/retry", dependencies=[require_roles("ops")])
async def admin_retry_dead_task(
    task_id: int, body: OutboxRetryRequest, session: DbSession, request: Request
) -> OutboxTaskStatusOut:
    """重放死信(需原因,与忽略对齐):置回 pending 交还 worker(handler 幂等,重放安全)。"""
    from app.core.outbox import OutboxTask
    from app.core.timeutil import now_utc

    task = await session.get(OutboxTask, task_id)
    if task is None:
        raise AppError(ErrorCode.NOT_FOUND, key="adminapi.taskNotFound", http_status=404)
    if task.status != "dead":
        raise AppError(
            ErrorCode.CONFLICT,
            key="adminapi.taskStateNotReplayable",
            params={"status": task.status},
        )
    task.status = "pending"
    task.retries = 0
    task.next_retry_at = now_utc()
    task.locked_by = None
    task.locked_at = None
    await session.commit()
    set_audit_target(
        request, f"outbox:{task_id}", detail={"type": task.type, "reason": body.reason}
    )
    return OutboxTaskStatusOut(id=task.id, status=task.status)


@router.post("/outbox/{task_id}/discard", dependencies=[require_roles("ops")])
async def admin_discard_dead_task(
    task_id: int, body: OutboxDiscardRequest, session: DbSession, request: Request
) -> OutboxTaskStatusOut:
    """忽略死信(需原因):确认该任务不再需要执行(如实例已人工处理)。"""
    from app.core.outbox import OutboxTask

    task = await session.get(OutboxTask, task_id)
    if task is None:
        raise AppError(ErrorCode.NOT_FOUND, key="adminapi.taskNotFound", http_status=404)
    if task.status != "dead":
        raise AppError(
            ErrorCode.CONFLICT, key="adminapi.taskStateNotIgnorable", params={"status": task.status}
        )
    task.status = "discarded"
    await session.commit()
    set_audit_target(request, f"outbox:{task_id}", detail={"reason": body.reason})
    return OutboxTaskStatusOut(id=task.id, status=task.status)
