from typing import Literal

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from app.core.audit import set_audit_target
from app.core.config import get_settings
from app.core.csvexport import CSV_RESPONSES, csv_response
from app.core.db import DbSession
from app.core.http import mark_idempotent_replay
from app.core.money import as_amount
from app.core.pagination import Page
from app.core.params import Cursor, ExportLang, IdempotencyKey, Limit, TzOffset
from app.core.platform_config import get_runtime_config, recharge_presets_of
from app.core.ratelimit import check_rate_limit
from app.core.timeutil import billing_month_range, parse_local_date
from app.modules.account import service as account_service
from app.modules.account.deps import CurrentUser
from app.modules.billing import export as billing_export, invoices, payment_service, refunds, wallet
from app.modules.billing.schemas import (
    BillHourlyOut,
    BillSummaryOut,
    DailySummaryOut,
    InvoiceCreate,
    InvoiceEligibleOut,
    InvoiceOut,
    LedgerEntryOut,
    PoliciesOut,
    RechargeCreate,
    RechargeOut,
    RefundableOrderOut,
    RefundCreate,
    RefundOut,
    WalletOut,
)

router = APIRouter(tags=["billing"])


@router.get("/policies")
async def get_policies(session: DbSession) -> PoliciesOut:
    """Billing / reclamation policies. Public; env defaults + DB overrides, adjusted online in the
    admin console."""
    p = await get_runtime_config(session)
    cfg = p
    settings = get_settings()
    return PoliciesOut(
        disk_price_gb_month=p.disk_price_gb_month,
        disk_min_gb=p.disk_min_gb,
        disk_max_gb=p.disk_max_gb,
        disk_grace_days=p.disk_grace_days,
        disk_frozen_days=p.disk_frozen_days,
        freeze_grace_hours=p.freeze_grace_hours,
        period_discount_day=p.period_discount_day,
        period_discount_week=p.period_discount_week,
        period_discount_month=p.period_discount_month,
        period_discount_year=p.period_discount_year,
        period_expire_warn_days=p.period_expire_warn_days,
        spot_discount_pct=p.spot_discount_pct,
        spot_grace_seconds=p.spot_grace_seconds,
        real_name_enabled=cfg.real_name_enabled,
        real_name_required_for_recharge=cfg.real_name_required_for_recharge,
        currency=settings.platform_currency,
        billing_timezone=settings.billing_timezone,
        recharge_min=as_amount(p.recharge_min),
        recharge_max=as_amount(p.recharge_max),
        recharge_presets=[as_amount(v) for v in recharge_presets_of(p)],
    )


@router.get("/wallet")
async def get_wallet(user: CurrentUser, session: DbSession) -> WalletOut:
    w = await wallet.get_or_create_wallet(session, user.id)
    await session.commit()
    return WalletOut.model_validate(w)


@router.get("/wallet/ledger")
async def get_ledger(
    user: CurrentUser,
    session: DbSession,
    cursor: str | None = Cursor,
    limit: int | None = Limit,
) -> Page[LedgerEntryOut]:
    return await wallet.ledger_page(session, user.id, cursor=cursor, limit=limit)


@router.get("/bills/hourly")
async def list_hourly_bills(
    user: CurrentUser,
    session: DbSession,
    instance_id: int | None = None,
    month: str | None = None,
    tz_offset_minutes: int = TzOffset,
    cursor: str | None = Cursor,
    limit: int | None = Limit,
) -> Page[BillHourlyOut]:
    return await wallet.hourly_bills_page(
        session,
        user.id,
        instance_id=instance_id,
        month_range=billing_month_range(month, tz_offset_minutes=tz_offset_minutes)
        if month is not None
        else None,
        cursor=cursor,
        limit=limit,
    )


@router.get("/bills/summary")
async def bill_summary(
    user: CurrentUser, session: DbSession, month: str, tz_offset_minutes: int = TzOffset
) -> BillSummaryOut:
    """Monthly summary + cost attribution per instance. Window cut at the local month boundary."""
    start, end = billing_month_range(month, tz_offset_minutes=tz_offset_minutes)
    s = await wallet.consumption_summary(session, user.id, start, end)
    return BillSummaryOut(
        month=month, gpu_total=s.gpu_total, disk_total=s.disk_total, items=s.items
    )


@router.get("/bills/daily-summary")
async def bill_daily_summary(
    user: CurrentUser,
    session: DbSession,
    date: str,
    tz_offset_minutes: int = TzOffset,
) -> DailySummaryOut:
    """Today's consumption, local day boundary via tz_offset (default: the billing zone's current
    offset)."""
    start, end = parse_local_date(date, tz_offset_minutes)
    s = await wallet.consumption_summary(session, user.id, start, end)
    return DailySummaryOut(date=date, gpu_total=s.gpu_total, disk_total=s.disk_total, items=s.items)


@router.get(
    "/billing/export",
    responses=CSV_RESPONSES,
)
async def export_billing(
    user: CurrentUser,
    session: DbSession,
    dataset: Literal["hourly", "ledger"] = "hourly",
    month: str | None = None,
    tz_offset_minutes: int = TzOffset,
    lang: str = ExportLang,
) -> StreamingResponse:
    """Billing CSV export (streamed). month applies to hourly only; hard row cap, when hit the file
    ends with the
    #SUPERDL_EXPORT_TRUNCATED# marker row."""
    if dataset == "hourly":
        stream = billing_export.stream_hourly_csv(
            session,
            user.id,
            month_range=billing_month_range(month, tz_offset_minutes=tz_offset_minutes)
            if month is not None
            else None,
            tz_offset_minutes=tz_offset_minutes,
            lang=lang,
        )
    else:
        stream = billing_export.stream_ledger_csv(
            session, user.id, tz_offset_minutes=tz_offset_minutes, lang=lang
        )
    return csv_response(stream, f"superdl-{dataset}-{month or 'all'}.csv")


@router.post("/wallet/recharges", status_code=201)
async def create_recharge(
    body: RechargeCreate,
    user: CurrentUser,
    session: DbSession,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> RechargeOut:
    await account_service.require_real_name_if_required(
        session, user, key="billing.realNameRequiredForRecharge"
    )
    await check_rate_limit(f"billing-recharge:{user.id}", max_attempts=10, window_seconds=3600.0)
    order, created = await payment_service.create_recharge(
        session, user.id, body.amount, body.channel, idempotency_key
    )
    if not created:
        mark_idempotent_replay(response)
    set_audit_target(request, f"order:{order.order_no}")
    return payment_service.to_recharge_out(order)


@router.get("/wallet/recharges/{order_no}")
async def get_recharge(order_no: str, user: CurrentUser, session: DbSession) -> RechargeOut:
    order = await payment_service.get_order(session, user.id, order_no)
    return payment_service.to_recharge_out(order)


@router.get("/wallet/refunds/eligible-orders")
async def list_refundable_orders(user: CurrentUser, session: DbSession) -> list[RefundableOrderOut]:
    """Refund form candidates: recent top-up orders each marked refundable or not (with a reason
    code)."""
    rows = await refunds.refundable_orders(session, user.id)
    return [RefundableOrderOut.model_validate(r) for r in rows]


@router.post("/wallet/refunds", status_code=201)
async def create_refund(
    body: RefundCreate,
    user: CurrentUser,
    session: DbSession,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> RefundOut:
    """Request a refund. An Idempotency-Key replay returns the existing request (200 +
    X-Idempotent-Replay);
    an active request on the same order is caught by the partial unique index."""
    await check_rate_limit(f"billing-refund:{user.id}", max_attempts=10, window_seconds=3600.0)
    req, created = await refunds.create_refund(
        session,
        user.id,
        order_no=body.order_no,
        amount=body.amount,
        reason=body.reason,
        idempotency_key=idempotency_key,
    )
    if not created:
        mark_idempotent_replay(response)
    set_audit_target(request, f"refund:{req.id}", detail={"refund_no": req.refund_no})
    return RefundOut.model_validate(req)


@router.get("/wallet/refunds")
async def list_my_refunds(
    user: CurrentUser,
    session: DbSession,
    cursor: str | None = Cursor,
    limit: int | None = Limit,
) -> Page[RefundOut]:
    """The caller's refund requests (cursor pagination)."""
    return await refunds.list_my_refunds(session, user.id, cursor=cursor, limit=limit)


@router.get("/billing/invoices/eligible")
async def list_invoice_eligible(user: CurrentUser, session: DbSession) -> list[InvoiceEligibleOut]:
    """Invoiceable amount preview per period (finished periods with amount > 0 only)."""
    return await invoices.eligible_periods(session, user.id)


@router.post("/billing/invoices", status_code=201)
async def create_invoice(
    body: InvoiceCreate,
    user: CurrentUser,
    session: DbSession,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> InvoiceOut:
    """Request an invoice. amount is computed server-side per period; an Idempotency-Key replay
    returns the existing request
    (200 + X-Idempotent-Replay)."""
    req, created = await invoices.create_invoice(
        session,
        user.id,
        period=body.period,
        title_type=body.title_type,
        title=body.title,
        tax_id=body.tax_id,
        email=body.email,
        idempotency_key=idempotency_key,
    )
    if not created:
        mark_idempotent_replay(response)
    set_audit_target(request, f"invoice:{req.id}")
    return InvoiceOut.model_validate(req)


@router.get("/billing/invoices")
async def list_my_invoices(
    user: CurrentUser,
    session: DbSession,
    cursor: str | None = Cursor,
    limit: int | None = Limit,
) -> Page[InvoiceOut]:
    """The caller's invoice requests (cursor pagination)."""
    return await invoices.list_my_invoices(session, user.id, cursor=cursor, limit=limit)
