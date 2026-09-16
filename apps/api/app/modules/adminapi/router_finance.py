"""Admin routes (reconciliation / alerts / adjustments / refunds / invoices / orders / revenue /
backfill)."""

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import mark_audited_read, set_audit_target, write_audit_sync
from app.core.csvexport import CSV_RESPONSES, csv_response
from app.core.db import DbSession, get_sessionmaker
from app.core.handles import mask_handle
from app.core.http import mark_idempotent_replay
from app.core.metrics import PII_REVEAL_ROWS_TOTAL
from app.core.pagination import Page
from app.core.params import Cursor, IdempotencyKey, Limit, TzOffset
from app.core.ratelimit import check_rate_limit
from app.modules.account import service as account_service
from app.modules.adminapi import auth_service, export as admin_export, finance_service
from app.modules.adminapi.deps import CurrentAdmin, require_roles
from app.modules.adminapi.models import AdminUser
from app.modules.adminapi.router_shared import DayRange, ExportLang, day_suffix, parse_day
from app.modules.adminapi.schemas import (
    REASON_MAX_LENGTH,
    AdjustmentOut,
    AdjustmentStatusOut,
    AdminAlertOut,
    AdminOrderOut,
    AlertUnreadCountOut,
    OrderBackfillOut,
    OrderVerifyOut,
    PaymentAnomalyOut,
    ReasonBody,
    RevenueReportOut,
)
from app.modules.billing import service as billing_service
from app.modules.billing.schemas import (
    AdminInvoiceOut,
    AdminRefundOut,
    AdminSettlementGapOut,
    InvoiceIssue,
    InvoiceReject,
    RefundCancel,
    RefundPayout,
    RefundReview,
    SettlementGapResolve,
)
from app.modules.metering import service as metering_service
from app.modules.metering.schemas import ReconciliationOut
from app.modules.notify import service as notify_service

if TYPE_CHECKING:
    from app.modules.notify.models import Notification

router = APIRouter(tags=["admin"])


@router.get("/reconciliation", dependencies=[require_roles("finance", "readonly")])
async def reconciliation(session: DbSession, day: str) -> ReconciliationOut:
    """Daily reconciliation: event billing vs metric estimate + diff % (> 2 % lists the divergent
    instances)."""
    day_start, _ = parse_day(day)
    report = await metering_service.reconciliation_report(session, day_start)
    return report


@router.get(
    "/reconciliation/export",
    dependencies=[require_roles("finance", "readonly")],
    responses=CSV_RESPONSES,
)
async def reconciliation_export(
    session: DbSession, day: str, lang: str = ExportLang
) -> StreamingResponse:
    """Daily reconciliation CSV: the same report as GET /reconciliation (total first + instances
    above the diff threshold)."""
    day_start, _ = parse_day(day)
    report = await metering_service.reconciliation_report(session, day_start)
    return csv_response(
        admin_export.stream_reconciliation_csv(report, lang=lang),
        f"superdl-reconciliation-{day}.csv",
    )


def _alert_out(r: "Notification", usernames: dict[int, str]) -> AdminAlertOut:
    return AdminAlertOut(
        id=r.id,
        type=r.type,
        title=r.title,
        content=r.content,
        severity=r.severity,
        created_at=r.created_at.isoformat(),
        acked_by=r.acked_by,
        acked_by_username=usernames.get(r.acked_by) if r.acked_by is not None else None,
        acked_at=r.acked_at.isoformat() if r.acked_at else None,
        target_kind=r.target_kind,
        target_id=r.target_id,
    )


async def _ack_usernames(session: AsyncSession, rows: "list[Notification]") -> dict[int, str]:
    """Acknowledger id → username."""
    ids = {r.acked_by for r in rows if r.acked_by is not None}
    if not ids:
        return {}
    pairs = (
        await session.execute(
            sa_select(AdminUser.id, AdminUser.username).where(AdminUser.id.in_(ids))
        )
    ).all()
    return {row.id: row.username for row in pairs}


@router.get("/alerts", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_alerts(
    session: DbSession,
    severity: str | None = None,
    alert_type: Literal["admin_alert", "gpu_fault"] | None = Query(default=None, alias="type"),
    acked: bool | None = None,
    cursor: str | None = Cursor,
    limit: int | None = Limit,
) -> Page[AdminAlertOut]:
    """Admin alert feed (cursor pagination, descending): severity / type / ack exact filters."""
    page = await notify_service.admin_alert_stream(
        session,
        severity=severity,
        alert_type=alert_type,
        acked=acked,
        cursor=cursor,
        limit=limit,
    )
    usernames = await _ack_usernames(session, page.items)
    return Page[AdminAlertOut](
        items=[_alert_out(r, usernames) for r in page.items], next_cursor=page.next_cursor
    )


@router.get("/alerts/unread-count", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_alerts_unread_count(session: DbSession) -> AlertUnreadCountOut:
    """Unacknowledged alert count (separate endpoint); critical_count feeds the overview KPI."""
    total, critical = await notify_service.unread_alert_count(session)
    return AlertUnreadCountOut(count=total, critical_count=critical)


@router.post("/alerts/{alert_id}/ack", dependencies=[require_roles("ops")])
async def admin_ack_alert(
    alert_id: int, admin: CurrentAdmin, session: DbSession, request: Request
) -> AdminAlertOut:
    """Acknowledge an alert (roles: ops/admin): records who and when; repeating → 409."""
    row = await notify_service.ack_admin_alert(session, alert_id, acked_by=admin.id)
    set_audit_target(request, f"alert:{alert_id}")
    usernames = await _ack_usernames(session, [row])
    return _alert_out(row, usernames)


class AdjustmentCreate(BaseModel):
    user_id: int
    amount: str = Field(pattern=r"^-?(0|[1-9]\d{0,11})(\.\d{1,2})?$")
    reason: str = Field(min_length=2, max_length=256)


class AdjustmentReview(BaseModel):
    approve: bool
    comment: str | None = Field(default=None, max_length=256)


class ReversalResolve(BaseModel):
    """Channel reversal write-off: release = noise, unfreeze (the order regains refund eligibility);
    chargeback = confirmed reversal, unfreeze + debit the same amount."""

    action: Literal["release", "chargeback"]
    reason: str = Field(min_length=2, max_length=256)


@router.get("/adjustments", dependencies=[require_roles("finance", "readonly")])
async def admin_list_adjustments(
    session: DbSession,
    status: str | None = None,
    user_id: int | None = None,
    day_range: DayRange = None,
    cursor: str | None = Cursor,
    limit: int | None = Limit,
) -> Page[AdjustmentOut]:
    """Adjustment list (cursor pagination, descending). status/user_id exact filters; day=YYYY-MM-DD
    filters by initiation day."""
    return await finance_service.list_adjustments(
        session,
        status=status,
        user_id=user_id,
        day_range=day_range,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/adjustments/export",
    dependencies=[require_roles("finance", "readonly")],
    responses=CSV_RESPONSES,
)
async def admin_adjustments_export(
    session: DbSession,
    status: str | None = None,
    user_id: int | None = None,
    day_range: DayRange = None,
    tz_offset_minutes: int = TzOffset,
    lang: str = ExportLang,
) -> StreamingResponse:
    """Adjustment CSV (streamed): the same filters as GET /adjustments; hard row cap + truncation
    marker row.
    Must be registered before /adjustments/{adjustment_id}."""
    return csv_response(
        admin_export.stream_adjustments_csv(
            session,
            status=status,
            user_id=user_id,
            day_range=day_range,
            tz_offset_minutes=tz_offset_minutes,
            lang=lang,
        ),
        f"superdl-adjustments-{day_suffix(day_range)}.csv",
    )


@router.post("/adjustments", status_code=201)
async def admin_create_adjustment(
    body: AdjustmentCreate,
    session: DbSession,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
    admin: AdminUser = require_roles("finance"),
) -> AdjustmentStatusOut:
    """Initiate an adjustment (two-person review follows). An Idempotency-Key replay returns the
    accepted request (200 + X-Idempotent-Replay)."""
    await check_rate_limit(f"admin-adjust:{admin.id}", max_attempts=20, window_seconds=3600.0)
    adj, created = await finance_service.create_adjustment(
        session,
        user_id=body.user_id,
        amount=body.amount,
        reason=body.reason,
        created_by=admin.id,
        idempotency_key=idempotency_key,
    )
    if not created:
        mark_idempotent_replay(response)
    set_audit_target(request, f"adjustment:{adj.id}", detail={"amount": str(adj.amount)})
    return AdjustmentStatusOut(id=adj.id, status=adj.status)


@router.post("/adjustments/{adjustment_id}/review")
async def admin_review_adjustment(
    adjustment_id: int,
    body: AdjustmentReview,
    session: DbSession,
    request: Request,
    admin: AdminUser = require_roles("finance"),
) -> AdjustmentStatusOut:
    """Review an adjustment (approve takes effect at once): the audit row and the effect share one
    transaction (write_audit_sync)."""
    set_audit_target(request, f"adjustment:{adjustment_id}", detail={"approve": body.approve})
    adj = await finance_service.review_adjustment(
        session,
        adjustment_id,
        approve=body.approve,
        reviewer_id=admin.id,
        comment=body.comment,
        audit_writer=lambda s: write_audit_sync(request, s),
    )
    return AdjustmentStatusOut(id=adj.id, status=adj.status)


@router.post("/finance/reversals/{order_no}/resolve", status_code=200)
async def admin_resolve_reversal(
    order_no: str,
    body: ReversalResolve,
    session: DbSession,
    request: Request,
    admin: AdminUser = require_roles("finance"),
) -> dict[str, str]:
    """Write off a channel reversal (channel_reversed bucket): unfreeze, or unfreeze + debit the
    same
    amount; audit row and write-off in one transaction."""
    set_audit_target(
        request, f"reversal:{order_no}", detail={"action": body.action, "reason": body.reason}
    )
    await finance_service.resolve_reversal(
        session,
        order_no,
        action=body.action,
        reason=body.reason,
        operator_id=admin.id,
        audit_writer=lambda s: write_audit_sync(request, s),
    )
    return {"status": "resolved", "action": body.action}


@router.get("/refunds", dependencies=[require_roles("finance", "readonly")])
async def admin_list_refunds(
    session: DbSession,
    status: str | None = None,
    day_range: DayRange = None,
    cursor: str | None = Cursor,
    limit: int | None = Limit,
) -> Page[AdminRefundOut]:
    """Refund list (cursor pagination, descending). day=YYYY-MM-DD filters by request time."""
    return await billing_service.admin_list_refunds(
        session, status, day_range=day_range, cursor=cursor, limit=limit
    )


@router.get(
    "/refunds/export",
    dependencies=[require_roles("finance", "readonly")],
    responses=CSV_RESPONSES,
)
async def admin_refunds_export(
    session: DbSession,
    status: str | None = None,
    day_range: DayRange = None,
    tz_offset_minutes: int = TzOffset,
    lang: str = ExportLang,
) -> StreamingResponse:
    """Refund CSV (streamed): the same filters as GET /refunds; hard row cap + truncation marker
    row.
    Must be registered before /refunds/{refund_id}."""
    return csv_response(
        billing_service.stream_admin_refunds_csv(
            session,
            status=status,
            day_range=day_range,
            tz_offset_minutes=tz_offset_minutes,
            lang=lang,
        ),
        f"superdl-refunds-{day_suffix(day_range)}.csv",
    )


@router.post("/refunds/{refund_id}/review")
async def admin_review_refund(
    refund_id: int,
    body: RefundReview,
    session: DbSession,
    request: Request,
    admin: AdminUser = require_roles("finance"),
) -> AdminRefundOut:
    """Review (approve / reject both need a comment). Approval ≠ payout: only sets approved and
    waits
    for another finance admin to register the payout."""
    req = await billing_service.review_refund(
        session, refund_id, approve=body.approve, comment=body.comment, reviewer_id=admin.id
    )
    set_audit_target(
        request,
        f"refund:{req.id}",
        detail={"refund_no": req.refund_no, "approve": body.approve, "comment": body.comment},
    )
    return await billing_service.admin_refund_out(session, req)


@router.post("/refunds/{refund_id}/payout")
async def admin_payout_refund(
    refund_id: int,
    body: RefundPayout,
    session: DbSession,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
    admin: AdminUser = require_roles("finance"),
) -> AdminRefundOut:
    """Register the payout (the only money-out point): two people enforced (same person as the
    reviewer → 409); insufficient balance → 409.
    Audit row and payout in one transaction (write_audit_sync). Idempotency-Key: same key and params
    replay 200 + X-Idempotent-Replay,
    same key with different params → 409."""
    set_audit_target(
        request,
        f"refund:{refund_id}",
        detail={"channel": body.channel, "ref": body.ref},
    )
    req, replayed = await billing_service.payout_refund(
        session,
        refund_id,
        channel=body.channel,
        ref=body.ref,
        operator_id=admin.id,
        audit_writer=lambda s: write_audit_sync(request, s),
        idempotency_key=idempotency_key,
    )
    if replayed:
        mark_idempotent_replay(response)
    return await billing_service.admin_refund_out(session, req)


@router.post("/refunds/{refund_id}/cancel", dependencies=[require_roles("finance")])
async def admin_cancel_refund(
    refund_id: int,
    body: RefundCancel,
    session: DbSession,
    request: Request,
) -> AdminRefundOut:
    """Cancel a refund request (pending/approved only). The wallet is untouched."""
    req = await billing_service.cancel_refund(session, refund_id)
    set_audit_target(
        request, f"refund:{req.id}", detail={"refund_no": req.refund_no, "reason": body.reason}
    )
    return await billing_service.admin_refund_out(session, req)


def _invoice_reveal(admin: AdminUser, reveal: bool, reason: str | None) -> str:
    """With reveal, check role and reason and return the normalised reason, otherwise the empty
    string."""
    return auth_service.ensure_reveal_allowed(role=admin.role, reason=reason) if reveal else ""


@router.get("/invoices")
async def admin_list_invoices(
    session: DbSession,
    request: Request,
    status: str | None = None,
    period: str | None = None,
    reveal: bool = False,
    reason: str | None = Query(default=None, max_length=REASON_MAX_LENGTH),
    admin: AdminUser = require_roles("finance"),
) -> list[AdminInvoiceOut]:
    """Invoice request list (fixed cap 200). status/period (YYYY-MM) exact filters.

    Title and email are masked by default; reveal=true + reason returns plaintext, audited with row
    count and reason.
    """
    reveal_reason = _invoice_reveal(admin, reveal, reason)
    rows = await billing_service.admin_list_invoices(session, status=status, period=period)
    if not reveal:
        return [
            r.model_copy(
                update={
                    "title": account_service.mask_id_name(r.title),
                    "email": mask_handle(r.email),
                }
            )
            for r in rows
        ]
    PII_REVEAL_ROWS_TOTAL.labels(kind="invoice_identity").inc(len(rows))
    mark_audited_read(
        request,
        "invoice-identity:reveal",
        detail={
            "rows": len(rows),
            "reason": reveal_reason,
            "status": status,
            "period": period,
            "format": "json",
        },
    )
    return rows


@router.get(
    "/invoices/export",
    responses=CSV_RESPONSES,
)
async def admin_invoices_export(
    session: DbSession,
    request: Request,
    status: str | None = None,
    period: str | None = None,
    tz_offset_minutes: int = TzOffset,
    lang: str = ExportLang,
    reveal: bool = False,
    reason: str | None = Query(default=None, max_length=REASON_MAX_LENGTH),
    admin: AdminUser = require_roles("finance"),
) -> StreamingResponse:
    """Invoice request CSV (streamed): the same filters as GET /invoices; hard row cap + truncation
    marker row.
    Must be registered before /invoices/{invoice_id}. Masked by default, plaintext needs reveal + a
    reason; every export is audited.
    """
    reveal_reason = _invoice_reveal(admin, reveal, reason)
    detail: dict[str, Any] = {
        "rows": 0,
        "reveal": reveal,
        "reason": reveal_reason,
        "status": status,
        "period": period,
        "format": "csv",
    }
    mark_audited_read(request, "invoice-identity:export", detail=detail)
    stream = billing_service.stream_admin_invoices_csv(
        session,
        status=status,
        period=period,
        tz_offset_minutes=tz_offset_minutes,
        lang=lang,
        reveal=reveal,
        row_counter=detail,
    )
    return csv_response(
        _count_revealed_rows(stream, detail) if reveal else stream,
        f"superdl-invoices-{period or 'all'}.csv",
    )


async def _count_revealed_rows(
    stream: AsyncIterator[str], detail: dict[str, Any]
) -> AsyncIterator[str]:
    """Plaintext export rows go into the metric (the number of rows actually sent)."""
    try:
        async for chunk in stream:
            yield chunk
    finally:
        PII_REVEAL_ROWS_TOTAL.labels(kind="invoice_identity").inc(detail["rows"])


@router.post("/invoices/{invoice_id}/issue")
async def admin_issue_invoice(
    invoice_id: int,
    body: InvoiceIssue,
    session: DbSession,
    request: Request,
    admin: AdminUser = require_roles("finance"),
) -> AdminInvoiceOut:
    """Issue: record the invoice number and notify the user in-app."""
    req = await billing_service.issue_invoice(
        session, invoice_id, invoice_no=body.invoice_no, operator_id=admin.id
    )
    set_audit_target(
        request, f"invoice:{req.id}", detail={"period": req.period, "invoice_no": body.invoice_no}
    )
    return AdminInvoiceOut.model_validate(req)


@router.post("/invoices/{invoice_id}/reject")
async def admin_reject_invoice(
    invoice_id: int,
    body: InvoiceReject,
    session: DbSession,
    request: Request,
    admin: AdminUser = require_roles("finance"),
) -> AdminInvoiceOut:
    """Reject (reason required): notify the user in-app; the same period can be requested again."""
    req = await billing_service.reject_invoice(
        session, invoice_id, reason=body.reason, operator_id=admin.id
    )
    set_audit_target(
        request, f"invoice:{req.id}", detail={"period": req.period, "reason": body.reason}
    )
    return AdminInvoiceOut.model_validate(req)


@router.get("/orders", dependencies=[require_roles("finance", "readonly")])
async def admin_list_orders(
    session: DbSession,
    status: str | None = None,
    order_no: str | None = None,
    user_id: int | None = None,
    day_range: DayRange = None,
    cursor: str | None = Cursor,
    limit: int | None = Limit,
) -> Page[AdminOrderOut]:
    """Top-up order list (cursor pagination, descending). order_no exact; day=YYYY-MM-DD filters by
    order day (UTC)."""
    page = await billing_service.admin_list_orders(
        session,
        status,
        order_no=order_no,
        user_id=user_id,
        day_range=day_range,
        cursor=cursor,
        limit=limit,
    )
    return Page[AdminOrderOut](
        items=[
            AdminOrderOut(**billing_service.to_recharge_out(r).model_dump(), user_id=r.user_id)
            for r in page.items
        ],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/orders/export",
    dependencies=[require_roles("finance", "readonly")],
    responses=CSV_RESPONSES,
)
async def admin_orders_export(
    session: DbSession,
    status: str | None = None,
    order_no: str | None = None,
    user_id: int | None = None,
    day_range: DayRange = None,
    tz_offset_minutes: int = TzOffset,
    lang: str = ExportLang,
) -> StreamingResponse:
    """Top-up order CSV (streamed): the same filters as GET /orders; hard row cap + truncation
    marker
    row."""
    return csv_response(
        billing_service.stream_admin_orders_csv(
            session,
            status=status,
            order_no=order_no,
            user_id=user_id,
            day_range=day_range,
            tz_offset_minutes=tz_offset_minutes,
            lang=lang,
        ),
        f"superdl-orders-{day_suffix(day_range)}.csv",
    )


@router.get("/reports/revenue", dependencies=[require_roles("ops", "finance", "readonly")])
async def revenue_report(session: DbSession, tz_offset_minutes: int = TzOffset) -> RevenueReportOut:
    """Today's / this month's consumption (absolute ledger consume) and new sign-ups. Local day
    boundary via tz_offset."""
    revenue = await billing_service.revenue_summary(session, tz_offset_minutes=tz_offset_minutes)
    signups = await account_service.signup_counts(session, tz_offset_minutes=tz_offset_minutes)
    return RevenueReportOut.model_validate({**revenue, **signups})


@router.get("/finance/anomalies", dependencies=[require_roles("finance", "readonly")])
async def admin_payment_anomalies(session: DbSession) -> list[PaymentAnomalyOut]:
    """Anomaly list: suspected lost callbacks / orders closed in the last 48 h / negative
    wallets."""
    rows = await billing_service.list_payment_anomalies(session)
    return [PaymentAnomalyOut.model_validate(r) for r in rows]


@router.post("/finance/orders/{order_no}/verify", dependencies=[require_roles("finance")])
async def admin_verify_order(order_no: str, session: DbSession, request: Request) -> OrderVerifyOut:
    """Verify the order status and amount with the channel (backfill precondition)."""
    result = await billing_service.verify_order(session, order_no)
    set_audit_target(
        request, f"order:{order_no}", detail={"channel_status": result["channel_status"]}
    )
    return OrderVerifyOut.model_validate(result)


class OrderBackfillRequest(ReasonBody):
    pass


@router.post("/finance/orders/{order_no}/backfill", dependencies=[require_roles("finance")])
async def admin_backfill_order(
    order_no: str,
    body: OrderBackfillRequest,
    session: DbSession,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> OrderBackfillOut:
    """Manual backfill: credits only after a live channel check confirms paid with a matching
    amount.
    A replay with the same idempotency key returns the current state (X-Idempotent-Replay).
    Audit row and credit in one transaction (write_audit_sync)."""
    set_audit_target(request, f"order:{order_no}", detail={"reason": body.reason})
    order, replayed = await billing_service.backfill_order(
        session,
        order_no,
        idempotency_key=idempotency_key,
        audit_writer=lambda s: write_audit_sync(request, s),
    )
    if replayed:
        mark_idempotent_replay(response)
    return OrderBackfillOut(order_no=order.order_no, status=order.status)


@router.get("/finance/settlement-gaps", dependencies=[require_roles("finance", "readonly")])
async def admin_list_settlement_gaps(
    session: DbSession,
    kind: Literal["hourly", "daily_disk"] | None = None,
    reason: str | None = None,
    unresolved: bool = True,
    cursor: str | None = Cursor,
    limit: int | None = Limit,
) -> Page[AdminSettlementGapOut]:
    """Gap list (cursor pagination, descending): unresolved only by default."""
    return await billing_service.admin_list_gaps(
        session, kind=kind, reason=reason, unresolved_only=unresolved, cursor=cursor, limit=limit
    )


@router.post("/finance/settlement-gaps/{gap_id}/replay")
async def admin_replay_settlement_gap(
    gap_id: int,
    request: Request,
    admin: AdminUser = require_roles("finance"),
) -> AdminSettlementGapOut:
    """Replay the idempotent posting primitive of the gap window (manual trigger): success writes
    resolved_at.
    grace_overlap gaps and vanished objects are 409 and go to manual write-off."""
    out = await billing_service.replay_gap(get_sessionmaker(), gap_id, operator_id=admin.id)
    set_audit_target(
        request,
        f"settlement_gap:{gap_id}",
        detail={"action": "replay", "kind": out.kind, "reason": out.reason},
    )
    return out


@router.post("/finance/settlement-gaps/{gap_id}/resolve")
async def admin_resolve_settlement_gap(
    gap_id: int,
    body: SettlementGapResolve,
    session: DbSession,
    request: Request,
    admin: AdminUser = require_roles("finance"),
) -> AdminSettlementGapOut:
    """Manual write-off (no replay). A note is required."""
    gap = await billing_service.resolve_gap(session, gap_id, note=body.note, operator_id=admin.id)
    set_audit_target(
        request, f"settlement_gap:{gap_id}", detail={"action": "resolve", "note": body.note}
    )
    return AdminSettlementGapOut.model_validate(gap)
