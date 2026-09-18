"""Admin routes (overview / tickets / audit / policies / platform config / announcements / outbox
dead letters)."""

import secrets
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog, mark_audited_read, set_audit_target
from app.core.compliance import current_profile
from app.core.config import get_settings
from app.core.csvexport import CSV_RESPONSES, csv_response
from app.core.db import DbSession
from app.core.email import EmailError, ensure_email_platform_quota, get_email_channel
from app.core.errors import AppError, ErrorCode, conflict
from app.core.handles import NormalizedEmail, NormalizedPhone, mask_handle
from app.core.http import mark_idempotent_replay
from app.core.outbox import OutboxTask
from app.core.pagination import Page, decode_cursor_int
from app.core.params import Cursor, IdempotencyKey, Limit, TzOffset
from app.core.platform_config import (
    PLATFORM_CONFIG_GROUPS,
    POLICY_GROUP,
    POLICY_KEYS,
    SETTING_SPECS,
    compute_config_warnings,
    effective_strings,
    get_runtime_config,
    list_platform_overrides,
    runtime_config_from_strings,
    secret_preview,
    set_platform_settings,
)
from app.core.ratelimit import check_rate_limit
from app.core.registry import probe_harbor
from app.core.servercopy import copy as server_copy
from app.core.sms import SmsError, ensure_sms_platform_quota, get_sms_channel
from app.core.timeutil import now_utc
from app.core.verification import code_email
from app.modules.adminapi import export as admin_export, overview
from app.modules.adminapi.deps import CurrentAdmin, require_roles
from app.modules.adminapi.models import AdminUser
from app.modules.adminapi.router_shared import ExportLang
from app.modules.adminapi.schemas import (
    AnnouncementOut,
    AnnouncementResultOut,
    AuditLogOut,
    DeadTaskOut,
    DeploymentIdentityOut,
    EmailTestOut,
    OutboxTaskStatusOut,
    OverviewOut,
    PlatformConfigItemOut,
    PlatformConfigOut,
    PlatformConfigWarningOut,
    PoliciesAdminOut,
    PolicySpecOut,
    ReasonBody,
    RegistryTestOut,
    SmsTestOut,
    UpdatedKeysOut,
)
from app.modules.billing import service as billing_service
from app.modules.notify import service as notify_service
from app.modules.tickets import service as tickets_service
from app.modules.tickets.schemas import (
    AdminTicketCountOut,
    AdminTicketDetailOut,
    AdminTicketOut,
    AdminTicketReply,
    AdminTicketStatusUpdate,
)

router = APIRouter(tags=["admin"])


@router.get("/overview", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_overview(session: DbSession) -> OverviewOut:
    """On-call first-screen aggregate: instance COUNT per status, paying tenant COUNT, per-pool GPU
    inventory. All exact counts."""
    return OverviewOut.model_validate(await overview.overview(session))


@router.get("/tickets", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_list_tickets(
    session: DbSession,
    status: str | None = None,
    category: str | None = None,
    user_id: int | None = None,
    ticket_no: str | None = None,
    cursor: str | None = Cursor,
    limit: int | None = Limit,
) -> Page[AdminTicketOut]:
    """Ticket list (cursor pagination, descending): status/category exact filters, user_id/ticket_no
    search."""
    return await tickets_service.admin_list_tickets(
        session,
        status=status,
        category=category,
        user_id=user_id,
        ticket_no=ticket_no,
        cursor=cursor,
        limit=limit,
    )


@router.get("/tickets/count", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_tickets_count(
    session: DbSession,
    status: str = "pending_staff",
    category: str | None = None,
) -> AdminTicketCountOut:
    """Pending ticket count (default pending_staff). Must be registered before
    /tickets/{ticket_id}."""
    return AdminTicketCountOut(
        count=await tickets_service.admin_count_tickets(session, status=status, category=category)
    )


@router.get("/tickets/{ticket_id}", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_get_ticket(ticket_id: int, session: DbSession) -> AdminTicketDetailOut:
    """Ticket detail + message stream (ascending by time)."""
    return await tickets_service.admin_get_ticket(session, ticket_id)


@router.post("/tickets/{ticket_id}/reply")
async def admin_reply_ticket(
    ticket_id: int,
    body: AdminTicketReply,
    session: DbSession,
    request: Request,
    admin: AdminUser = require_roles("ops"),
) -> AdminTicketDetailOut:
    """Staff reply (→ pending_user), in-app notification to the user; resolved/closed accept no more
    replies."""
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
    """Mark resolved / close (close only after resolved; closed_at is set only on closed)."""
    ticket = await tickets_service.admin_update_status(session, ticket_id, action=body.action)
    set_audit_target(request, f"ticket:{ticket_id}", detail={"action": body.action, "by": admin.id})
    return AdminTicketOut.model_validate(ticket)


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
    """Audit search: actor_id / action prefix / time range; cursor pages backwards (a full page
    means
    older rows exist)."""
    stmt = admin_export.audit_filters(
        sa_select(AuditLog).order_by(AuditLog.id.desc()).limit(limit),
        actor_type=actor_type,
        actor_id=actor_id,
        q=q,
        since=since,
        until=until,
    )
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
    responses=CSV_RESPONSES,
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
    lang: str = ExportLang,
) -> StreamingResponse:
    """Audit search CSV (streamed): the same filters as GET /audit; hard row cap + truncation marker
    row.
    Writes one search audit row (filter parameters only)."""
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


@router.get("/policies", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_get_policies(session: DbSession) -> PoliciesAdminOut:
    """Current effective policies + ranges + DB overrides (the policy-group slice of the platform
    configuration)."""
    effective = await effective_strings(session)
    overrides = await list_platform_overrides(session)
    return PoliciesAdminOut(
        effective={k: effective[k] for k in POLICY_KEYS},
        overrides={k: overrides[k].value for k in POLICY_KEYS if k in overrides},
        specs={
            k: PolicySpecOut(
                kind=SETTING_SPECS[k].kind,
                min=None if SETTING_SPECS[k].lo is None else str(SETTING_SPECS[k].lo),
                max=None if SETTING_SPECS[k].hi is None else str(SETTING_SPECS[k].hi),
            )
            for k in POLICY_KEYS
        },
    )


class PolicyUpdateRequest(BaseModel):
    updates: dict[str, str] = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=2, max_length=200)


POLICY_ALERT_KEYS: frozenset[str] = frozenset(
    {
        "spot_discount_pct",
        "disk_price_gb_month",
        "max_gpus_per_user",
        "disk_grace_days",
        "disk_frozen_days",
        "period_discount_day",
        "period_discount_week",
        "period_discount_month",
        "period_discount_year",
    }
)
POLICY_ALERT_RATIO = Decimal("0.5")


def large_policy_moves(
    before: dict[str, str], after: dict[str, str], keys: Iterable[str]
) -> list[str]:
    """Sensitive policy keys that changed ≥ 50 % against the previous effective value, formatted
    `key: old → new`."""
    moves: list[str] = []
    for key in sorted(keys):
        if key not in POLICY_ALERT_KEYS or not before.get(key) or not after.get(key):
            continue
        old, new = Decimal(before[key]), Decimal(after[key])
        if old != 0 and abs(new - old) / old >= POLICY_ALERT_RATIO:
            moves.append(f"{key}: {old} → {new}")
    return moves


@router.put("/policies", dependencies=[require_roles()])
async def admin_update_policies(
    body: PolicyUpdateRequest, session: DbSession, request: Request, admin: CurrentAdmin
) -> UpdatedKeysOut:
    """Adjust policy parameters online (admin only, 20 per admin per hour; effective at once; policy
    group keys only).
    The audit detail records the old and new values and the reason; a sensitive key changing ≥ 50 %
    writes a critical admin alert in the same transaction."""
    await check_rate_limit(f"admin-pricing:{admin.id}", max_attempts=20, window_seconds=3600.0)
    before_all = await effective_strings(session)
    try:
        await set_platform_settings(
            session, body.updates, updated_by=None, allowed_groups=frozenset({POLICY_GROUP})
        )
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, str(exc)) from exc
    moves = large_policy_moves(before_all, await effective_strings(session), body.updates)
    if moves:
        await notify_service.notify(
            session,
            None,
            type_="admin_alert",
            title=server_copy("adminapi.policy_moves.title"),
            content=server_copy(
                "adminapi.policy_moves.content", moves="; ".join(moves), reason=body.reason
            ),
            severity="critical",
        )
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


@router.get("/platform-config", dependencies=[require_roles()])
async def admin_get_platform_config(session: DbSession) -> PlatformConfigOut:
    """Grouped settings: effective value + source (env default / DB override) + configuration risk
    warnings. Secrets return the last-4 preview only."""
    eff = await effective_strings(session)
    overrides = await list_platform_overrides(session)
    items = []
    for key, spec in SETTING_SPECS.items():
        if spec.group == "policy" or spec.kind in ("int", "decimal"):
            continue
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
    effective_cfg = runtime_config_from_strings(eff)
    warnings = [
        PlatformConfigWarningOut(key=w.key, level=w.level, message=w.message)
        for w in (
            *compute_config_warnings(effective_cfg, get_settings().environment),
            *billing_service.payment_config_warnings(effective_cfg, get_settings().environment),
        )
    ]
    settings = get_settings()
    return PlatformConfigOut(
        items=items,
        warnings=warnings,
        deployment=DeploymentIdentityOut(
            compliance_profile=current_profile().name,
            currency=settings.platform_currency,
            billing_timezone=settings.billing_timezone,
        ),
    )


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
    """Configure channel credentials and compliance information online (empty string = clear the
    override, back to the env default).

    The audit records key names and action type (set/clear), never values.
    """
    try:
        await set_platform_settings(
            session,
            body.updates,
            updated_by=admin.id,
            allowed_groups=frozenset(PLATFORM_CONFIG_GROUPS),
        )
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, str(exc)) from exc
    await session.commit()
    set_audit_target(
        request,
        "platform_config",
        detail={
            "keys": {k: ("clear" if v.strip() == "" else "set") for k, v in body.updates.items()},
            "reason": body.reason,
        },
    )
    return UpdatedKeysOut(updated=sorted(body.updates))


class SmsTestRequest(BaseModel):
    phone: NormalizedPhone


@router.post("/platform-config/test-sms", dependencies=[require_roles()])
async def admin_test_sms(body: SmsTestRequest, session: DbSession, request: Request) -> SmsTestOut:
    """Send one real verification SMS through the effective SMS configuration (rate-limited,
    audited)."""
    await check_rate_limit("admin:test-sms", max_attempts=10, window_seconds=3600.0)
    await ensure_sms_platform_quota()
    cfg = await get_runtime_config(session)
    code = f"{secrets.randbelow(10**6):06d}"
    try:
        channel = await get_sms_channel(session)
        await channel.send(body.phone, "verify", {"code": code})
    except SmsError as exc:
        raise AppError(
            ErrorCode.CODE_SEND_FAILED,
            key="adminapi.smsTestFailed",
            params={"message": str(exc)},
            http_status=502,
        ) from exc
    set_audit_target(request, f"test-sms:{body.phone}")
    return SmsTestOut(ok=True, provider=cfg.sms_provider)


class EmailTestRequest(BaseModel):
    email: NormalizedEmail


@router.post("/platform-config/test-email", dependencies=[require_roles()])
async def admin_test_email(
    body: EmailTestRequest, session: DbSession, request: Request
) -> EmailTestOut:
    """Send one test verification email through the effective email configuration
    (rate-limited, audited)."""
    await check_rate_limit("admin:test-email", max_attempts=10, window_seconds=3600.0)
    await ensure_email_platform_quota()
    cfg = await get_runtime_config(session)
    content = code_email("test", f"{secrets.randbelow(10**6):06d}")
    try:
        channel = await get_email_channel(session)
        await channel.send(body.email, content.subject, content.text, content.html)
    except EmailError as exc:
        raise AppError(
            ErrorCode.CODE_SEND_FAILED,
            key="adminapi.emailTestFailed",
            params={"message": str(exc)},
            http_status=502,
        ) from exc
    set_audit_target(request, f"test-email:{mask_handle(body.email)}")
    return EmailTestOut(ok=True, provider=cfg.email_provider)


@router.post("/platform-config/test-registry", dependencies=[require_roles()])
async def admin_test_registry(session: DbSession, request: Request) -> RegistryTestOut:
    """Probe Harbor with the effective registry configuration: health → robot-authenticated project
    repository listing.
    Read-only, rate-limited, audited."""
    await check_rate_limit("admin:test-registry", max_attempts=10, window_seconds=3600.0)
    cfg = await get_runtime_config(session)
    if not cfg.registry_host:
        raise AppError(
            ErrorCode.VALIDATION_ERROR, "fill in and save the Harbor address (registry_host) first"
        )
    probe = await probe_harbor(
        host=cfg.registry_host,
        project=cfg.registry_project or "superdl",
        robot=cfg.registry_robot_name,
        secret=cfg.registry_robot_secret,
        ca_pem=cfg.registry_ca_pem,
    )
    set_audit_target(request, f"test-registry:{cfg.registry_host}")
    return RegistryTestOut(
        ok=probe.ok,
        step=probe.step,
        detail=probe.detail,
        harbor_version=probe.harbor_version,
        repositories=probe.repositories,
    )


class AnnouncementCreate(BaseModel):
    title: str = Field(min_length=2, max_length=128)
    content: str = Field(min_length=2, max_length=2000)


class AnnouncementRevoke(ReasonBody):
    pass


def _announcement_out(a: Any) -> AnnouncementOut:
    return AnnouncementOut(
        id=a.id,
        title=a.title,
        content=a.content,
        status=a.status,
        reached=a.reached,
        created_by=a.created_by,
        created_at=a.created_at.isoformat(),
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
    idempotency_key: IdempotencyKey = None,
) -> AnnouncementResultOut:
    """Announcement broadcast (announcement-type notifications to every active user); writes the
    announcement-level record.
    An Idempotency-Key replay creates no announcement, returns 200 + X-Idempotent-Replay."""
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
    """Announcement history (withdrawn included; fixed cap 200)."""
    return [_announcement_out(a) for a in await notify_service.admin_list_announcements(session)]


@router.post("/announcements/{announcement_id}/revoke", dependencies=[require_roles("ops")])
async def admin_revoke_announcement(
    announcement_id: int,
    body: AnnouncementRevoke,
    admin: CurrentAdmin,
    session: DbSession,
    request: Request,
) -> AnnouncementOut:
    """Withdraw an announcement (reason required): tenants no longer see it. Repeating → 409."""
    announcement = await notify_service.revoke_announcement(
        session, announcement_id, revoked_by=admin.id, reason=body.reason
    )
    set_audit_target(request, f"announcement:{announcement_id}", detail={"reason": body.reason})
    return _announcement_out(announcement)


@router.get("/outbox/dead", dependencies=[require_roles("ops", "readonly")])
async def admin_list_dead_tasks(session: DbSession) -> list[DeadTaskOut]:
    """Dead-letter task list (the outbox_dead_total metric feeds the alert)."""
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


class OutboxDiscardRequest(ReasonBody):
    pass


class OutboxRetryRequest(ReasonBody):
    pass


async def _load_dead_task(session: AsyncSession, task_id: int, *, conflict_key: str) -> OutboxTask:
    """Load the dead task; missing → 404, a non-dead status raises CONFLICT with conflict_key."""
    task = await session.get(OutboxTask, task_id)
    if task is None:
        raise AppError(ErrorCode.NOT_FOUND, key="adminapi.taskNotFound", http_status=404)
    if task.status != "dead":
        raise conflict(key=conflict_key, params={"status": task.status})
    return task


@router.post("/outbox/{task_id}/retry", dependencies=[require_roles("ops")])
async def admin_retry_dead_task(
    task_id: int, body: OutboxRetryRequest, session: DbSession, request: Request
) -> OutboxTaskStatusOut:
    """Replay a dead letter (reason required): back to pending for the worker (handlers are
    idempotent)."""
    task = await _load_dead_task(session, task_id, conflict_key="adminapi.taskStateNotReplayable")
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
    """Discard a dead letter (reason required)."""
    task = await _load_dead_task(session, task_id, conflict_key="adminapi.taskStateNotIgnorable")
    task.status = "discarded"
    await session.commit()
    set_audit_target(request, f"outbox:{task_id}", detail={"reason": body.reason})
    return OutboxTaskStatusOut(id=task.id, status=task.status)
