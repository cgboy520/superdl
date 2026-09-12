"""退款闭环:申请 → 审批(finance) → 登记打款(双人) → 钱包核销。

不变量:
- 审批通过 ≠ 出金。只有 payout_refund 成功才同事务做钱包负向调账(ledger type='refund'),
  并回写 wallet_entry_id。
- 打款强制双人:payout_by ≠ review_by(应用层 409 + DB CHECK)。
- 打款时在钱包行锁内再校验可用余额 ≥ 退款额,不足 409,可取消该单。
- 出金的审计行与业务同事务(audit_writer 钩子,commit 前调用)。
"""

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
from app.core.money import as_amount, money_str
from app.core.pagination import Page, paginate_by_id
from app.core.sqlutil import get_for_update_or_404, next_daily_seq
from app.core.timeutil import now_utc
from app.modules.billing import invoices, wallet
from app.modules.billing.models import InvoiceRequest, Order, RefundRequest, reversal_blocks_refund
from app.modules.billing.schemas import AdminRefundOut, RefundOut

logger = get_logger(__name__)

# 活跃口径 = pending/approved:已打款不占位,同单可多次部分退款,累计上限 = 订单额 − Σpaid
ACTIVE_STATUSES = ("pending", "approved")

# 原路退回映射:订单支付渠道 → 打款渠道(offline 例外)
_CHANNEL_TO_PAYOUT = {"wechat": "wechat_transfer", "alipay": "alipay_transfer"}

# 用户端「可申请订单」候选集:最近 N 笔充值订单(含不可申请行)
REFUNDABLE_ORDERS_CAP = 50


async def _order_has_issued_invoice(
    session: AsyncSession, order: Order, *, lock: bool = False
) -> bool:
    """发票联动:该订单 paid 账期(北京时间)存在 status='issued' 的发票申请即「已开票」,不可退。
    仅拦截 issued。lock=True 对该账期的活跃申请行 FOR UPDATE,与 issue_invoice 的行锁串行。
    """
    if order.paid_at is None:
        return False
    period = invoices.beijing_period(order.paid_at)
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
    """该订单已打款退款合计。"""
    total = (
        await session.execute(
            select(func.coalesce(func.sum(RefundRequest.amount), 0)).where(
                RefundRequest.order_no == order_no,
                RefundRequest.status == "paid",
            )
        )
    ).scalar_one()
    return Decimal(total)


@dataclass(frozen=True)
class RefundLimit:
    """退款上限的三个分量(用户端候选集与申请校验共用同一口径):
    订单剩余可退 = 订单额 − Σ已打款退款;可用余额 = balance − frozen(负数按 0);可退余额见
    wallet.refundable_capacity。上限 = 三者取小。"""

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
    """用户申请退款。幂等:Idempotency-Key 重放返回既有单;同键异参 409。同单可多次部分退款。
    返回 (退款单, created):created=False = 幂等重放,路由回 200 + X-Idempotent-Replay。"""
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
            return existing, False  # 幂等重放
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
                "max": money_str(limit.amount),
                "order": money_str(order.amount),
                "refunded": money_str(refunded),
                "refundable": money_str(limit.refundable),
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
    """申请前置:本人已支付订单、未被渠道冲正、账期未开票、无在途退款单。"""
    order = (
        await session.execute(
            select(Order).where(Order.order_no == order_no, Order.user_id == user_id)
        )
    ).scalar_one_or_none()
    if order is None:
        # 他人的订单号同样回 404
        raise AppError(ErrorCode.ORDER_NOT_FOUND, key="billing.orderNotFound", http_status=404)
    if order.status != "paid":
        raise conflict(key="billing.refundOrderNotPaid")
    # 渠道冲正(待处置/已坐实)后禁止平台侧二次退款出金;人工 release 的恢复资格
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
    """落退款单:refund_no = R+yyyymmdd+两位日内序列;并发同序列由唯一索引兜底,
    撞车换下一个序列重试。"""
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
                # 撞部分唯一索引(并发重复申请同一订单)
                raise conflict(key="billing.refundAlreadyApplied") from None
            continue  # refund_no 序列撞车:重试下一序列
        if result is not req:
            return result, False  # 同键并发:返回胜出方
        logger.info("refund_created", refund_no=req.refund_no, order_no=order_no)
        return req, True
    raise AppError(ErrorCode.INTERNAL, key="common.internal", http_status=500)


async def list_my_refunds(
    session: AsyncSession, user_id: int, *, cursor: str | None = None, limit: int | None = None
) -> Page[RefundOut]:
    """本人退款单(游标分页)。"""
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
    """用户端退款表单的订单候选集:最近充值订单逐单标注可否申请与置灰原因。
    max_amount = min(订单剩余可退, 当前余额),订单剩余可退 = 订单额 − Σ已打款退款。
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


# ---------- 管理端(finance/admin) ----------


def admin_refunds_query(
    *, status: str | None = None, day_range: tuple[datetime, datetime] | None = None
) -> Select[tuple[RefundRequest]]:
    """管理端退款单的筛选口径(列表与 CSV 共用):status 精确;
    day_range 为 [start, end) 的申请时间窗口。"""
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
    """退款单列表(游标分页,降序)。day_range 为 [start, end) 的 created_at 窗口。"""
    stmt = admin_refunds_query(status=status, day_range=day_range).order_by(RefundRequest.id.desc())
    page_items, next_cursor = await paginate_by_id(
        session, stmt, id_col=RefundRequest.id, cursor=cursor, limit=limit
    )
    return Page[AdminRefundOut](
        items=[AdminRefundOut.model_validate(r) for r in page_items], next_cursor=next_cursor
    )


async def review_refund(
    session: AsyncSession,
    refund_id: int,
    *,
    approve: bool,
    comment: str,
    reviewer_id: int,
) -> RefundRequest:
    """审批(行锁内状态迁移)。通过 ≠ 出金:只置 approved。"""
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
    """登记打款:唯一出金点。同事务完成钱包负向调账 + 状态置 paid + 回写 wallet_entry_id。
    audit_writer 在 commit 前调用,写失败即整体回滚。

    幂等(Idempotency-Key,与申请键分列),行锁内判定:已 paid 且键匹配 → 重放返回 (req, True);
    键匹配但指纹不符 → 409;无键的重复打款走状态机 409(refundStateNotPayable)。"""
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
        remark=f"退款 {req.refund_no}(订单 {req.order_no})",
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
        await audit_writer(session)  # 与出金同事务
    await session.commit()
    logger.info("refund_paid", refund_no=req.refund_no, channel=channel)
    return req, False


async def _assert_payable(
    session: AsyncSession, req: RefundRequest, *, channel: str, operator_id: int
) -> None:
    """出金前的六道闸(顺序即优先级):状态 approved → 双人制衡 → 订单未被渠道冲正 → 原路退回渠道
    → 累计已退不超订单额 → 钱包行锁内可用余额与可退余额都够。全部在退款单行锁内执行。"""
    if req.status != "approved":
        raise conflict(key="billing.refundStateNotPayable", params={"status": req.status})
    if req.review_by == operator_id:
        # 双人制衡(DB 另有 CHECK payout_not_reviewer)
        raise conflict(key="billing.refundPayoutSamePerson")
    order = (
        await session.execute(select(Order).where(Order.order_no == req.order_no))
    ).scalar_one_or_none()
    if order is not None:
        # 出金前复核订单未被渠道冲正
        if reversal_blocks_refund(order):
            raise conflict(key="billing.refundChannelReversed")
        # 原路退回:打款渠道须与订单支付渠道同源;offline 是唯一例外;mock 不映射,放行
        expected_payout = _CHANNEL_TO_PAYOUT.get(order.channel)
        if expected_payout is not None and channel not in (expected_payout, "offline"):
            raise conflict(
                key="billing.refundPayoutChannelMismatch",
                params={"expected": expected_payout},
            )
        # 多次部分退款的出金闸:累计已退 + 本单 ≤ 订单额
        paid_total = await _paid_total_of_order(session, req.order_no)
        if paid_total + req.amount > order.amount:
            raise conflict(
                key="billing.refundCumulativeExceeded",
                params={
                    "order": money_str(order.amount),
                    "refunded": money_str(paid_total),
                    "amount": money_str(req.amount),
                },
            )
    # 不复查账期是否已开票(打款的退款在开票重算时已从票额扣除)。
    # 钱包行锁内再校验可用余额(balance - frozen),不足不出金
    locked = await wallet.lock_wallet(session, req.user_id)
    if wallet.available_of(locked) < req.amount:
        raise conflict(
            key="billing.refundBalanceConsumed",
            params={
                "balance": money_str(wallet.available_of(locked)),
                "amount": money_str(req.amount),
            },
        )
    # 可退余额硬闸(wallet.refundable_capacity 口径)
    refundable = await wallet.refundable_capacity(session, req.user_id)
    if refundable < req.amount:
        raise conflict(
            key="billing.refundNotRefundable",
            params={"refundable": money_str(refundable), "amount": money_str(req.amount)},
        )


async def cancel_refund(session: AsyncSession, refund_id: int) -> RefundRequest:
    """取消(仅 pending/approved)。不动钱包。"""
    req = await get_for_update_or_404(
        session, RefundRequest, refund_id, key="billing.refundNotFound"
    )
    if req.status not in ACTIVE_STATUSES:
        raise conflict(key="billing.refundStateNotCancellable", params={"status": req.status})
    req.status = "cancelled"
    await session.commit()
    logger.info("refund_cancelled", refund_no=req.refund_no)
    return req
