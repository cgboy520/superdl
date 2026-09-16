"""Invoice requests, issuance and rejection for finished calendar months of the billing zone.

At most one submitted/issued request per user and period; issuing recomputes the amount under the
request row lock and rejects on mismatch.
"""

import re
from decimal import Decimal

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.compliance import current_profile
from app.core.config import get_settings
from app.core.constants import ADMIN_LIST_CAP
from app.core.errors import AppError, ErrorCode, conflict
from app.core.idempotency import find_replay, insert_idempotent, request_fingerprint
from app.core.logging import get_logger
from app.core.money import as_amount, money_label
from app.core.pagination import Page, paginate_by_id
from app.core.regions import cn
from app.core.servercopy import copy as server_copy
from app.core.sqlutil import get_for_update_or_404, sum_decimal, total
from app.core.timeutil import billing_period_range, current_billing_period, now_utc
from app.modules.billing.models import InvoiceRequest, Order, RefundRequest
from app.modules.billing.schemas import AdminInvoiceOut, InvoiceEligibleOut, InvoiceOut
from app.modules.notify import service as notify_service

logger = get_logger(__name__)

ACTIVE_STATUSES = ("submitted", "issued")

#: Tax-ID format per `ComplianceProfile.invoice_tax_id_rule`: (regex, error key). Profiles without
#: a rule accept any 2–32 character identifier as typed.
TAX_ID_RULES: dict[str, tuple[str, str]] = {
    "cn_uscc": (cn.USCC_RE, "billing.invoiceTaxIdInvalidCn"),
}


def normalize_tax_id(title_type: str, tax_id: str | None) -> str | None:
    """Apply the profile's tax-ID rule to a company title (the PRC code is upper-cased before the
    check); personal titles carry none."""
    if title_type != "company" or tax_id is None:
        return None
    rule = current_profile().invoice_tax_id_rule
    if rule is None:
        return tax_id
    pattern, key = TAX_ID_RULES[rule]
    candidate = tax_id.upper()
    if not re.fullmatch(pattern, candidate):
        raise AppError(ErrorCode.VALIDATION_ERROR, key=key)
    return candidate


REFUND_WITHHELD_STATUSES = ("pending", "approved", "paid")


async def _period_paid_sum(session: AsyncSession, user_id: int, period: str) -> Decimal:
    """Paid top-ups within the period (calendar month of the billing zone); reversed orders count
    only with action='release'."""
    start, end = billing_period_range(period)
    return await sum_decimal(
        session,
        select(total(Order.amount)).where(
            Order.user_id == user_id,
            Order.status == "paid",
            or_(Order.channel_reversed_at.is_(None), Order.channel_reversal_action == "release"),
            Order.paid_at >= start,
            Order.paid_at < end,
        ),
    )


async def _period_refund_sum(session: AsyncSession, user_id: int, period: str) -> Decimal:
    """Refund total of the period's orders (paid + pending/approved in flight), attributed by the
    linked order's paid_at."""
    start, end = billing_period_range(period)
    return await sum_decimal(
        session,
        select(total(RefundRequest.amount))
        .join(Order, RefundRequest.order_no == Order.order_no)
        .where(
            RefundRequest.user_id == user_id,
            RefundRequest.status.in_(REFUND_WITHHELD_STATUSES),
            Order.paid_at >= start,
            Order.paid_at < end,
        ),
    )


async def _period_active_sum(session: AsyncSession, user_id: int, period: str) -> Decimal:
    """Amount already used in the period: submitted + issued request amounts."""
    return await sum_decimal(
        session,
        select(total(InvoiceRequest.amount)).where(
            InvoiceRequest.user_id == user_id,
            InvoiceRequest.period == period,
            InvoiceRequest.status.in_(ACTIVE_STATUSES),
        ),
    )


async def _period_billable_amount(
    session: AsyncSession, user_id: int, period: str, *, excluding: Decimal = Decimal("0")
) -> Decimal:
    """Invoiceable amount of the period = countable top-ups − paid and in-flight refunds − active
    invoice amounts + excluding.

    excluding is this request's own amount, excluded when recomputing at issue time.
    """
    paid = await _period_paid_sum(session, user_id, period)
    refunded = await _period_refund_sum(session, user_id, period)
    active = await _period_active_sum(session, user_id, period)
    return as_amount(paid - refunded - (active - excluding))


async def _active_of_period(
    session: AsyncSession, user_id: int, period: str
) -> InvoiceRequest | None:
    return (
        await session.execute(
            select(InvoiceRequest).where(
                InvoiceRequest.user_id == user_id,
                InvoiceRequest.period == period,
                InvoiceRequest.status.in_(ACTIVE_STATUSES),
            )
        )
    ).scalar_one_or_none()


async def eligible_periods(session: AsyncSession, user_id: int) -> list[InvoiceEligibleOut]:
    """Invoiceable amount preview per period: finished periods with paid orders, computed one by
    one, only periods > 0 returned, descending."""
    period_col = func.to_char(
        func.timezone(get_settings().billing_timezone, Order.paid_at), "YYYY-MM"
    )
    periods = (
        (
            await session.execute(
                select(period_col)
                .where(
                    Order.user_id == user_id,
                    Order.status == "paid",
                    Order.paid_at.is_not(None),
                )
                .group_by(period_col)
                .order_by(period_col.desc())
            )
        )
        .scalars()
        .all()
    )
    current = current_billing_period()
    out: list[InvoiceEligibleOut] = []
    for period in periods:
        if period >= current:
            continue
        remaining = await _period_billable_amount(session, user_id, period)
        if remaining > 0:
            out.append(InvoiceEligibleOut(period=period, amount=remaining))
    return out


async def create_invoice(
    session: AsyncSession,
    user_id: int,
    *,
    period: str,
    title_type: str,
    title: str,
    tax_id: str | None,
    email: str,
    idempotency_key: str | None,
) -> tuple[InvoiceRequest, bool]:
    """Compute the amount and submit the invoice request, returning (request, created); an
    idempotent replay has created=False.
    Company tax IDs are validated by the compliance profile's rule (`TAX_ID_RULES`)."""
    tax_id = normalize_tax_id(title_type, tax_id)
    fingerprint = request_fingerprint(user_id, period, title_type, title, tax_id, email)
    if idempotency_key:
        existing = await find_replay(
            session,
            InvoiceRequest,
            owner_col=InvoiceRequest.user_id,
            owner_id=user_id,
            key=idempotency_key,
            fingerprint=fingerprint,
        )
        if existing is not None:
            return existing, False

    if period >= current_billing_period():
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="billing.invoicePeriodNotOpen",
            params={"period": period},
        )
    if await _active_of_period(session, user_id, period) is not None:
        raise conflict(key="billing.invoicePeriodAlreadyApplied", params={"period": period})
    amount = await _period_billable_amount(session, user_id, period)
    if amount <= 0:
        raise conflict(key="billing.invoiceNothingToBill", params={"period": period})

    req = InvoiceRequest(
        user_id=user_id,
        period=period,
        title_type=title_type,
        title=title,
        tax_id=tax_id,
        email=email,
        amount=amount,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
    )
    try:
        result = await insert_idempotent(
            session,
            req,
            model=InvoiceRequest,
            owner_col=InvoiceRequest.user_id,
            owner_id=user_id,
            key=idempotency_key,
            fingerprint=fingerprint,
            commit=True,
        )
    except IntegrityError:
        raise conflict(
            key="billing.invoicePeriodAlreadyApplied", params={"period": period}
        ) from None
    if result is not req:
        return result, False
    logger.info("invoice_created", invoice_id=req.id, user_id=user_id, period=period)
    return req, True


async def list_my_invoices(
    session: AsyncSession, user_id: int, *, cursor: str | None = None, limit: int | None = None
) -> Page[InvoiceOut]:
    """The caller's invoice requests (cursor pagination)."""
    stmt = (
        select(InvoiceRequest)
        .where(InvoiceRequest.user_id == user_id)
        .order_by(InvoiceRequest.id.desc())
    )
    page_items, next_cursor = await paginate_by_id(
        session, stmt, id_col=InvoiceRequest.id, cursor=cursor, limit=limit
    )
    return Page[InvoiceOut](
        items=[InvoiceOut.model_validate(r) for r in page_items], next_cursor=next_cursor
    )


def admin_invoices_query(
    *, status: str | None = None, period: str | None = None
) -> Select[tuple[InvoiceRequest]]:
    """Admin invoice request filters (shared by list and CSV): status / period exact."""
    stmt = select(InvoiceRequest)
    if status:
        stmt = stmt.where(InvoiceRequest.status == status)
    if period:
        stmt = stmt.where(InvoiceRequest.period == period)
    return stmt


async def admin_list_invoices(
    session: AsyncSession, status: str | None = None, period: str | None = None
) -> list[AdminInvoiceOut]:
    """Invoice request list (fixed cap). status/period exact filters."""
    stmt = (
        admin_invoices_query(status=status, period=period)
        .order_by(InvoiceRequest.id.desc())
        .limit(ADMIN_LIST_CAP)
    )
    rows = (await session.execute(stmt)).scalars()
    return [AdminInvoiceOut.model_validate(r) for r in rows]


async def issue_invoice(
    session: AsyncSession, invoice_id: int, *, invoice_no: str, operator_id: int
) -> InvoiceRequest:
    """Issue (status transition under the row lock): amount recomputation gate + invoice number +
    operator, in-app notification to the user."""
    req = await get_for_update_or_404(
        session, InvoiceRequest, invoice_id, key="billing.invoiceNotFound"
    )
    if req.status != "submitted":
        raise conflict(key="billing.invoiceStateNotIssuable", params={"status": req.status})
    current = await _period_billable_amount(session, req.user_id, req.period, excluding=req.amount)
    if current != req.amount:
        raise conflict(
            key="billing.invoiceAmountStale",
            params={"expected": money_label(current), "requested": money_label(req.amount)},
        )
    req.status = "issued"
    req.invoice_no = invoice_no
    req.issued_by = operator_id
    req.issued_at = now_utc()
    await notify_service.notify(
        session,
        req.user_id,
        type_="invoice",
        title=server_copy("billing.invoice_issued.title"),
        content=server_copy(
            "billing.invoice_issued.content",
            period=req.period,
            amount=money_label(req.amount),
            invoice_no=invoice_no,
            email=req.email,
        ),
        dedup_key=f"invoice:issued:{req.id}",
    )
    await session.commit()
    logger.info("invoice_issued", invoice_id=req.id, invoice_no=invoice_no)
    return req


async def reject_invoice(
    session: AsyncSession, invoice_id: int, *, reason: str, operator_id: int
) -> InvoiceRequest:
    """Lock and reject a submitted request, write the notification in the same transaction and
    commit; the caller validates the reason."""
    req = await get_for_update_or_404(
        session, InvoiceRequest, invoice_id, key="billing.invoiceNotFound"
    )
    if req.status != "submitted":
        raise conflict(key="billing.invoiceStateNotRejectable", params={"status": req.status})
    req.status = "rejected"
    req.reject_reason = reason
    await notify_service.notify(
        session,
        req.user_id,
        type_="invoice",
        title=server_copy("billing.invoice_rejected.title"),
        content=server_copy("billing.invoice_rejected.content", period=req.period, reason=reason),
        dedup_key=f"invoice:rejected:{req.id}",
    )
    await session.commit()
    logger.info("invoice_rejected", invoice_id=req.id, operator_id=operator_id)
    return req
