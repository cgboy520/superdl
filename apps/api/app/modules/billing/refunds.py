"""Refund requests, two-person approval and payout, wallet write-off; the payout audit must be
written through audit_writer in the same transaction."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode, conflict
from app.core.idempotency import find_replay, insert_idempotent, request_fingerprint
from app.core.logging import get_logger
from app.core.money import as_amount, money_label
from app.core.pagination import Page, paginate_by_id
from app.core.servercopy import copy as server_copy
from app.core.sqlutil import get_for_update_or_404, next_daily_seq, sum_decimal, total
from app.core.timeutil import billing_period, now_utc
from app.modules.billing import invoices, wallet
from app.modules.billing.models import InvoiceRequest, Order, RefundRequest, reversal_blocks_refund
from app.modules.billing.payment_channels import CHANNELS
from app.modules.billing.schemas import AdminRefundOut, RefundOut

logger = get_logger(__name__)

ACTIVE_STATUSES = ("pending", "approved")

REFUNDABLE_ORDERS_CAP = 50


async def _order_has_issued_invoice(
    session: AsyncSession, order: Order, *, lock: bool = False
) -> bool:
    """Invoice link: a status='issued' invoice request for the order's paid period (billing zone)
    means "invoiced" and blocks the refund.
    Only issued blocks. lock=True takes FOR UPDATE on the period's active request row, serialised
    with the issue_invoice row lock.
    """
    if order.paid_at is None:
        return False
    period = billing_period(order.paid_at)
    stmt = (
        select(InvoiceRequest.status)
        .where(
            InvoiceRequest.user_id == order.user_id,
            InvoiceRequest.period == period,
            InvoiceRequest.status.in_(invoices.ACTIVE_STATUSES),
        )
        .limit(1)
    )
    if lock:
        stmt = stmt.with_for_update()
    status = (await session.execute(stmt)).scalar_one_or_none()
    return status == "issued"


async def _active_refund_of_order(session: AsyncSession, order_no: str) -> RefundRequest | None:
    return (
        await session.execute(
            select(RefundRequest).where(
                RefundRequest.order_no == order_no,
                RefundRequest.status.in_(ACTIVE_STATUSES),
            )
        )
    ).scalar_one_or_none()


async def _paid_total_of_order(session: AsyncSession, order_no: str) -> Decimal:
    """Total paid refunds of the order."""
    return await sum_decimal(
        session,
        select(total(RefundRequest.amount)).where(
            RefundRequest.order_no == order_no, RefundRequest.status == "paid"
        ),
    )


@dataclass(frozen=True)
class RefundLimit:
    """The three components of the refund cap (the user-side candidate set and the request check
    share the definition):
    order remainder = order amount − Σ paid refunds; available balance = balance − frozen (negative
    → 0); refundable balance see
    wallet.refundable_capacity. Cap = the minimum of the three."""

    remaining: Decimal
    balance: Decimal
    refundable: Decimal

    @property
    def amount(self) -> Decimal:
        return min(self.remaining, self.balance, self.refundable)


def _refund_limit(
    order_amount: Decimal, refunded: Decimal, balance: Decimal, refundable: Decimal
) -> RefundLimit:
    return RefundLimit(
        remaining=as_amount(order_amount - refunded),
        balance=max(Decimal("0.00"), balance),
        refundable=refundable,
    )


async def create_refund(
    session: AsyncSession,
    user_id: int,
    *,
    order_no: str,
    amount: Decimal,
    reason: str,
    idempotency_key: str | None,
) -> tuple[RefundRequest, bool]:
    """Submit a refund request, returning (request, created); a replay with the same key and params
    has created=False, different params → 409."""
    amount = as_amount(amount)
    fingerprint = request_fingerprint(user_id, order_no, amount, reason)
    if idempotency_key:
        existing = await find_replay(
            session,
            RefundRequest,
            owner_col=RefundRequest.user_id,
            owner_id=user_id,
            key=idempotency_key,
            fingerprint=fingerprint,
        )
        if existing is not None:
            return existing, False
    order = await _refundable_order(session, user_id, order_no)
    refunded = await _paid_total_of_order(session, order_no)
    limit = _refund_limit(
        order.amount,
        refunded,
        await wallet.get_available_balance(session, user_id),
        await wallet.refundable_capacity(session, user_id),
    )
    if amount > limit.amount:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="billing.refundAmountExceeded",
            params={
                "max": money_label(limit.amount),
                "order": money_label(order.amount),
                "refunded": money_label(refunded),
                "refundable": money_label(limit.refundable),
            },
        )
    return await _insert_refund(
        session,
        user_id,
        order_no=order_no,
        amount=amount,
        reason=reason,
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
    )


async def _refundable_order(session: AsyncSession, user_id: int, order_no: str) -> Order:
    """Check the caller's paid order, no blocking reversal, not invoiced and no active refund; lock
    the period's active invoice row."""
    order = (
        await session.execute(
            select(Order).where(Order.order_no == order_no, Order.user_id == user_id)
        )
    ).scalar_one_or_none()
    if order is None:
        raise AppError(ErrorCode.ORDER_NOT_FOUND, key="billing.orderNotFound", http_status=404)
    if order.status != "paid":
        raise conflict(key="billing.refundOrderNotPaid")
    if reversal_blocks_refund(order):
        raise conflict(key="billing.refundChannelReversed")
    if await _order_has_issued_invoice(session, order, lock=True):
        raise conflict(key="billing.refundInvoiceIssued")
    if await _active_refund_of_order(session, order_no) is not None:
        raise conflict(key="billing.refundAlreadyApplied")
    return order


async def _insert_refund(
    session: AsyncSession,
    user_id: int,
    *,
    order_no: str,
    amount: Decimal,
    reason: str,
    idempotency_key: str | None,
    fingerprint: str,
) -> tuple[RefundRequest, bool]:
    """Insert the refund request; the number is R + UTC date + at least two sequence digits,
    sequence
    conflicts retried up to eight times."""
    prefix = f"R{now_utc():%Y%m%d}"
    for _ in range(8):
        seq = await next_daily_seq(session, RefundRequest.refund_no, prefix)
        req = RefundRequest(
            refund_no=f"{prefix}-{seq:02d}",
            user_id=user_id,
            order_no=order_no,
            amount=amount,
            reason=reason,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
        )
        try:
            result = await insert_idempotent(
                session,
                req,
                model=RefundRequest,
                owner_col=RefundRequest.user_id,
                owner_id=user_id,
                key=idempotency_key,
                fingerprint=fingerprint,
                commit=True,
            )
        except IntegrityError:
            if await _active_refund_of_order(session, order_no) is not None:
                raise conflict(key="billing.refundAlreadyApplied") from None
            continue
        if result is not req:
            return result, False
        logger.info("refund_created", refund_no=req.refund_no, order_no=order_no)
        return req, True
    raise AppError(ErrorCode.INTERNAL, key="common.internal", http_status=500)


async def list_my_refunds(
    session: AsyncSession, user_id: int, *, cursor: str | None = None, limit: int | None = None
) -> Page[RefundOut]:
    """The caller's refund requests (cursor pagination)."""
    stmt = (
        select(RefundRequest)
        .where(RefundRequest.user_id == user_id)
        .order_by(RefundRequest.id.desc())
    )
    page_items, next_cursor = await paginate_by_id(
        session, stmt, id_col=RefundRequest.id, cursor=cursor, limit=limit
    )
    return Page[RefundOut](
        items=[RefundOut.model_validate(r) for r in page_items], next_cursor=next_cursor
    )


async def refundable_orders(session: AsyncSession, user_id: int) -> list[dict]:
    """Refund candidate view of recent top-up orders with the reasons they cannot be requested.

    max_amount is the minimum of the order remainder, the non-negative available balance and the
    refundable ledger balance; this view does not check channel reversals.
    """
    orders = list(
        (
            await session.execute(
                select(Order)
                .where(Order.user_id == user_id)
                .order_by(Order.id.desc())
                .limit(REFUNDABLE_ORDERS_CAP)
            )
        ).scalars()
    )
    active_order_nos = set(
        (
            await session.execute(
                select(RefundRequest.order_no).where(
                    RefundRequest.user_id == user_id,
                    RefundRequest.status.in_(ACTIVE_STATUSES),
                )
            )
        ).scalars()
    )
    paid_rows = (
        await session.execute(
            select(RefundRequest.order_no, func.coalesce(func.sum(RefundRequest.amount), 0))
            .where(RefundRequest.user_id == user_id, RefundRequest.status == "paid")
            .group_by(RefundRequest.order_no)
        )
    ).all()
    paid_by_order: dict[str, Decimal] = {order_no: Decimal(total) for order_no, total in paid_rows}
    balance = await wallet.get_available_balance(session, user_id)
    refundable = await wallet.refundable_capacity(session, user_id)
    out: list[dict] = []
    for o in orders:
        limit = _refund_limit(
            o.amount, paid_by_order.get(o.order_no, Decimal("0.00")), balance, refundable
        )
        reason_code: str | None = None
        if o.status != "paid":
            reason_code = "not_paid"
        elif o.order_no in active_order_nos:
            reason_code = "already_applied"
        elif limit.remaining <= 0:
            reason_code = "fully_refunded"
        elif await _order_has_issued_invoice(session, o):
            reason_code = "invoiced"
        elif limit.balance <= 0 or limit.refundable <= 0:
            reason_code = "no_balance"
        out.append(
            {
                "order_no": o.order_no,
                "amount": o.amount,
                "channel": o.channel,
                "status": o.status,
                "paid_at": o.paid_at,
                "refundable": reason_code is None,
                "reason_code": reason_code,
                "max_amount": limit.amount,
            }
        )
    return out


def admin_refunds_query(
    *, status: str | None = None, day_range: tuple[datetime, datetime] | None = None
) -> Select[tuple[RefundRequest]]:
    """Admin refund request filters (shared by list and CSV): status exact;
    day_range is the [start, end) request-time window."""
    stmt = select(RefundRequest)
    if status:
        stmt = stmt.where(RefundRequest.status == status)
    if day_range is not None:
        stmt = stmt.where(
            RefundRequest.created_at >= day_range[0], RefundRequest.created_at < day_range[1]
        )
    return stmt


async def admin_list_refunds(
    session: AsyncSession,
    status: str | None = None,
    *,
    day_range: tuple[datetime, datetime] | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> Page[AdminRefundOut]:
    """Refund request list (cursor pagination, descending). day_range is the [start, end)
    created_at window."""
    stmt = admin_refunds_query(status=status, day_range=day_range).order_by(RefundRequest.id.desc())
    page_items, next_cursor = await paginate_by_id(
        session, stmt, id_col=RefundRequest.id, cursor=cursor, limit=limit
    )
    channels = await _order_channels(session, [r.order_no for r in page_items])
    return Page[AdminRefundOut](
        items=[_admin_out(r, channels.get(r.order_no)) for r in page_items],
        next_cursor=next_cursor,
    )


async def _order_channels(session: AsyncSession, order_nos: list[str]) -> dict[str, str]:
    if not order_nos:
        return {}
    rows = await session.execute(
        select(Order.order_no, Order.channel).where(Order.order_no.in_(order_nos))
    )
    return {row.order_no: row.channel for row in rows}


def _admin_out(req: RefundRequest, order_channel: str | None) -> AdminRefundOut:
    return AdminRefundOut.model_validate(req).model_copy(update={"order_channel": order_channel})


async def admin_refund_out(session: AsyncSession, req: RefundRequest) -> AdminRefundOut:
    """Admin view of one refund with the paying channel of its order (payout options)."""
    channels = await _order_channels(session, [req.order_no])
    return _admin_out(req, channels.get(req.order_no))


async def review_refund(
    session: AsyncSession,
    refund_id: int,
    *,
    approve: bool,
    comment: str,
    reviewer_id: int,
) -> RefundRequest:
    """Review (status transition under the row lock). Approval ≠ payout: only sets approved."""
    req = await get_for_update_or_404(
        session, RefundRequest, refund_id, key="billing.refundNotFound"
    )
    if req.status != "pending":
        raise conflict(key="billing.refundStateNotReviewable", params={"status": req.status})
    req.review_by = reviewer_id
    req.review_at = now_utc()
    req.review_comment = comment
    req.status = "approved" if approve else "rejected"
    await session.commit()
    logger.info("refund_reviewed", refund_no=req.refund_no, approve=approve)
    return req


async def payout_refund(
    session: AsyncSession,
    refund_id: int,
    *,
    channel: str,
    ref: str,
    operator_id: int,
    audit_writer: Callable[[AsyncSession], Awaitable[None]] | None = None,
    idempotency_key: str | None = None,
) -> tuple[RefundRequest, bool]:
    """Register the payout under the refund row lock; debit, set paid, link the ledger row and call
    the optional audit_writer in the same transaction, then commit.

    Already paid with a matching idempotency key returns (req, True); a stored fingerprint mismatch
    → 409.
    The first registration returns (req, False), other repeated payouts hit the status conflict.
    """
    req = await get_for_update_or_404(
        session, RefundRequest, refund_id, key="billing.refundNotFound"
    )
    fingerprint = request_fingerprint(refund_id, channel, ref, operator_id)
    if req.status == "paid" and idempotency_key and req.payout_idempotency_key == idempotency_key:
        stored = req.payout_request_fingerprint
        if stored is not None and stored != fingerprint:
            raise conflict(key="common.idempotencyKeyMismatch")
        return req, True
    await _assert_payable(session, req, channel=channel, operator_id=operator_id)
    entry = await wallet.debit(
        session,
        req.user_id,
        req.amount,
        type_="refund",
        ref_type="refund_request",
        ref_id=req.refund_no,
        remark=server_copy("billing.remark.refund", refund_no=req.refund_no, order_no=req.order_no),
        allow_negative=False,
    )
    req.status = "paid"
    req.payout_channel = channel
    req.payout_ref = ref
    req.payout_by = operator_id
    req.payout_at = now_utc()
    req.wallet_entry_id = entry.id
    if idempotency_key:
        req.payout_idempotency_key = idempotency_key
        req.payout_request_fingerprint = fingerprint
    if audit_writer is not None:
        await audit_writer(session)
    await session.commit()
    logger.info("refund_paid", refund_no=req.refund_no, channel=channel)
    return req, False


async def _assert_payable(
    session: AsyncSession, req: RefundRequest, *, channel: str, operator_id: int
) -> None:
    """The six gates before payout (in priority order): status approved → two-person rule → order
    not reversed by the channel → original channel
    → total refunded within the order amount → available and refundable balance sufficient under
    the wallet row lock. All under the refund row lock."""
    if req.status != "approved":
        raise conflict(key="billing.refundStateNotPayable", params={"status": req.status})
    if req.review_by == operator_id:
        raise conflict(key="billing.refundPayoutSamePerson")
    order = (
        await session.execute(select(Order).where(Order.order_no == req.order_no))
    ).scalar_one_or_none()
    if order is not None:
        if reversal_blocks_refund(order):
            raise conflict(key="billing.refundChannelReversed")
        order_spec = CHANNELS.get(order.channel)
        expected_payout = order_spec.payout_channel if order_spec is not None else None
        if expected_payout is not None and channel not in (expected_payout, "offline"):
            raise conflict(
                key="billing.refundPayoutChannelMismatch",
                params={"expected": expected_payout},
            )
        paid_total = await _paid_total_of_order(session, req.order_no)
        if paid_total + req.amount > order.amount:
            raise conflict(
                key="billing.refundCumulativeExceeded",
                params={
                    "order": money_label(order.amount),
                    "refunded": money_label(paid_total),
                    "amount": money_label(req.amount),
                },
            )
    locked = await wallet.lock_wallet(session, req.user_id)
    if wallet.available_of(locked) < req.amount:
        raise conflict(
            key="billing.refundBalanceConsumed",
            params={
                "balance": money_label(wallet.available_of(locked)),
                "amount": money_label(req.amount),
            },
        )
    refundable = await wallet.refundable_capacity(session, req.user_id)
    if refundable < req.amount:
        raise conflict(
            key="billing.refundNotRefundable",
            params={"refundable": money_label(refundable), "amount": money_label(req.amount)},
        )


async def cancel_refund(session: AsyncSession, refund_id: int) -> RefundRequest:
    """Cancel (pending/approved only). The wallet is untouched."""
    req = await get_for_update_or_404(
        session, RefundRequest, refund_id, key="billing.refundNotFound"
    )
    if req.status not in ACTIVE_STATUSES:
        raise conflict(key="billing.refundStateNotCancellable", params={"status": req.status})
    req.status = "cancelled"
    await session.commit()
    logger.info("refund_cancelled", refund_no=req.refund_no)
    return req
