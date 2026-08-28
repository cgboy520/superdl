"""退款闭环:申请 → 审批(finance) → 登记打款(双人) → 钱包核销。

关键不变量:
- 审批通过 ≠ 出金。只有 payout_refund 成功才在同一事务做钱包负向调账
  (balance_ledger type='refund',balance_after 快照),并回写 wallet_entry_id。
- 打款强制双人:payout_by ≠ review_by(应用层 409 + DB CHECK 双保险)。
- 打款时在钱包行锁内再校验余额 ≥ 退款额:审批后用户可能已消费,不足则 409,
  管理端可取消该单(余额不动)。
- 出金动作的审计行与业务同事务(audit_writer 钩子,commit 前调用):
  审计写失败即出金失败回滚——宁可不出金,不可无留痕。
"""

from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode, not_found
from app.core.idempotency import find_replay
from app.core.logging import get_logger
from app.core.money import as_amount
from app.core.pagination import Page, clamp_limit, decode_cursor_int, slice_page
from app.core.timeutil import now_utc
from app.modules.billing import invoices, wallet
from app.modules.billing.models import InvoiceRequest, Order, RefundRequest
from app.modules.billing.schemas import AdminRefundOut, RefundOut

logger = get_logger(__name__)

ACTIVE_STATUSES = ("pending", "approved", "paid")

# 用户端「可申请订单」候选集:最近 N 笔充值订单(含不可申请行,置灰展示用)
REFUNDABLE_ORDERS_CAP = 50


async def _order_has_issued_invoice(
    session: AsyncSession, order: Order, *, lock: bool = False
) -> bool:
    """发票联动:该订单所属用户、订单 paid 账期(北京时间)存在 status='issued' 的
    发票申请即视为「该账期已开票」——已开票账期的订单不可退,须先红冲
    (服务层抛 billing.refundInvoiceIssued,文案引导联系客服)。

    仅拦截 issued,submitted(申请中)不拦截。lock=True(申请退款时用)对该账期的活跃申请行
    FOR UPDATE,与 issue_invoice 的行锁串行;没有这道锁,申请与开票交错提交会让退款既不从
    票额扣除又能打款。
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


async def _next_daily_seq(session: AsyncSession, prefix: str) -> int:
    count = (
        await session.execute(
            select(func.count()).where(RefundRequest.refund_no.like(f"{prefix}-%"))
        )
    ).scalar_one()
    return count + 1


async def create_refund(
    session: AsyncSession,
    user_id: int,
    *,
    order_no: str,
    amount: Decimal,
    reason: str,
    idempotency_key: str | None,
) -> tuple[RefundRequest, bool]:
    """用户申请退款。幂等:Idempotency-Key 重放返回既有单(唯一约束兜底并发)。
    返回 (退款单, created):created=False = 幂等重放,路由回 200 + X-Idempotent-Replay。"""
    if idempotency_key:
        existing = await find_replay(
            session,
            RefundRequest,
            owner_col=RefundRequest.user_id,
            owner_id=user_id,
            key=idempotency_key,
        )
        if existing is not None:
            return existing, False  # 幂等重放

    order = (
        await session.execute(
            select(Order).where(Order.order_no == order_no, Order.user_id == user_id)
        )
    ).scalar_one_or_none()
    if order is None:
        # 他人的订单号对用户同样回 404(不泄露订单存在性,IDOR 防线)
        raise AppError(ErrorCode.ORDER_NOT_FOUND, key="billing.orderNotFound", http_status=404)
    if order.status != "paid":
        raise AppError(ErrorCode.CONFLICT, key="billing.refundOrderNotPaid", http_status=409)
    # 渠道冲正(用户已在微信/支付宝拒付拿回钱)后禁止平台侧二次退款出金——
    # 冲正只打标记不动余额(支付侧策略),出金口必须在此拦截
    if order.channel_reversed_at is not None:
        raise AppError(ErrorCode.CONFLICT, key="billing.refundChannelReversed", http_status=409)
    if await _order_has_issued_invoice(session, order, lock=True):
        raise AppError(ErrorCode.CONFLICT, key="billing.refundInvoiceIssued", http_status=409)
    if await _active_refund_of_order(session, order_no) is not None:
        raise AppError(ErrorCode.CONFLICT, key="billing.refundAlreadyApplied", http_status=409)

    amount = as_amount(amount)
    balance = await wallet.get_balance(session, user_id)
    limit = min(order.amount, balance)
    if amount > limit:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="billing.refundAmountExceeded",
            params={
                "max": format(limit, "f"),
                "order": format(order.amount, "f"),
                "balance": format(balance, "f"),
            },
        )

    # refund_no = R+yyyymmdd+两位日内序列。并发同序列由唯一索引兜底,撞车换下一个序列重试
    prefix = f"R{now_utc():%Y%m%d}"
    for _ in range(8):
        req = RefundRequest(
            refund_no=f"{prefix}-{await _next_daily_seq(session, prefix):02d}",
            user_id=user_id,
            order_no=order_no,
            amount=amount,
            reason=reason,
            idempotency_key=idempotency_key,
        )
        session.add(req)
        try:
            await session.commit()
            logger.info("refund_created", refund_no=req.refund_no, order_no=order_no)
            return req, True
        except IntegrityError:
            await session.rollback()
            if idempotency_key:
                winner = await find_replay(
                    session,
                    RefundRequest,
                    owner_col=RefundRequest.user_id,
                    owner_id=user_id,
                    key=idempotency_key,
                )
                if winner is not None:
                    return winner, False  # 同键并发:返回胜出方的单
            if await _active_refund_of_order(session, order_no) is not None:
                # 撞的是部分唯一索引(并发重复申请同一订单)
                raise AppError(
                    ErrorCode.CONFLICT, key="billing.refundAlreadyApplied", http_status=409
                ) from None
            # 否则按 refund_no 序列撞车处理:重试下一序列
    raise AppError(ErrorCode.INTERNAL, key="common.internal", http_status=500)


async def list_my_refunds(
    session: AsyncSession, user_id: int, *, cursor: str | None = None, limit: int | None = None
) -> Page[RefundOut]:
    """本人退款单(游标分页,语义与资金流水一致)。"""
    lim = clamp_limit(limit)
    stmt = (
        select(RefundRequest)
        .where(RefundRequest.user_id == user_id)
        .order_by(RefundRequest.id.desc())
        .limit(lim + 1)
    )
    last_id = decode_cursor_int(cursor)
    if last_id is not None:
        stmt = stmt.where(RefundRequest.id < last_id)
    rows = list((await session.execute(stmt)).scalars())
    page_items, next_cursor = slice_page(rows, lim, key=lambda r: r.id)
    return Page[RefundOut](
        items=[RefundOut.model_validate(r) for r in page_items], next_cursor=next_cursor
    )


async def refundable_orders(session: AsyncSession, user_id: int) -> list[dict]:
    """用户端退款表单的订单候选集:最近充值订单逐单标注可否申请与置灰原因。"""
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
    balance = await wallet.get_balance(session, user_id)
    out: list[dict] = []
    for o in orders:
        reason_code: str | None = None
        if o.status != "paid":
            reason_code = "not_paid"
        elif o.order_no in active_order_nos:
            reason_code = "already_applied"
        elif await _order_has_issued_invoice(session, o):
            reason_code = "invoiced"
        elif balance <= 0:
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
                "max_amount": min(o.amount, balance),
            }
        )
    return out


# ---------- 管理端(finance/admin) ----------


async def admin_list_refunds(
    session: AsyncSession,
    status: str | None = None,
    *,
    day_range: tuple[datetime, datetime] | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> Page[AdminRefundOut]:
    """退款单列表(游标分页,降序)。day_range 为 [start, end) 的 created_at 窗口。"""
    lim = clamp_limit(limit)
    stmt = select(RefundRequest).order_by(RefundRequest.id.desc()).limit(lim + 1)
    if status:
        stmt = stmt.where(RefundRequest.status == status)
    if day_range is not None:
        stmt = stmt.where(
            RefundRequest.created_at >= day_range[0], RefundRequest.created_at < day_range[1]
        )
    last_id = decode_cursor_int(cursor)
    if last_id is not None:
        stmt = stmt.where(RefundRequest.id < last_id)
    rows = list((await session.execute(stmt)).scalars())
    page_items, next_cursor = slice_page(rows, lim, key=lambda r: r.id)
    return Page[AdminRefundOut](
        items=[AdminRefundOut.model_validate(r) for r in page_items], next_cursor=next_cursor
    )


async def _get_for_update(session: AsyncSession, refund_id: int) -> RefundRequest:
    req = await session.get(RefundRequest, refund_id, with_for_update=True)
    if req is None:
        raise not_found(key="billing.refundNotFound")
    return req


async def review_refund(
    session: AsyncSession,
    refund_id: int,
    *,
    approve: bool,
    comment: str,
    reviewer_id: int,
) -> RefundRequest:
    """审批(行锁内做状态迁移)。通过 ≠ 出金:只置 approved,等登记打款。"""
    req = await _get_for_update(session, refund_id)
    if req.status != "pending":
        raise AppError(
            ErrorCode.CONFLICT,
            key="billing.refundStateNotReviewable",
            params={"status": req.status},
            http_status=409,
        )
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
) -> RefundRequest:
    """登记打款:唯一出金点。同事务完成钱包负向调账 + 状态置 paid + 回写 wallet_entry_id。
    audit_writer:同步审计钩子,最终 commit 前调用,写失败即整体回滚。"""
    req = await _get_for_update(session, refund_id)
    if req.status != "approved":
        raise AppError(
            ErrorCode.CONFLICT,
            key="billing.refundStateNotPayable",
            params={"status": req.status},
            http_status=409,
        )
    if req.review_by == operator_id:
        # 双人制衡硬要求(DB 还有 CHECK payout_not_reviewer 兜底)
        raise AppError(ErrorCode.CONFLICT, key="billing.refundPayoutSamePerson", http_status=409)
    # 审批到打款之间订单可能被渠道冲正(webhook 随时可达),出金前必须复核:
    # 用户已在渠道侧拿回钱的订单,平台再退一次 = 双重出金
    order = (
        await session.execute(select(Order).where(Order.order_no == req.order_no))
    ).scalar_one_or_none()
    if order is not None and order.channel_reversed_at is not None:
        raise AppError(ErrorCode.CONFLICT, key="billing.refundChannelReversed", http_status=409)
    # 不复查账期是否已开票:能走到打款的退款在开票重算时已从票额扣除
    # 钱包行锁内再校验:审批后用户可能已消费,余额不足坚决不出金(不允许负余额核销)
    locked = await wallet.lock_wallet(session, req.user_id)
    if locked.balance < req.amount:
        raise AppError(
            ErrorCode.CONFLICT,
            key="billing.refundBalanceConsumed",
            params={"balance": format(locked.balance, "f"), "amount": format(req.amount, "f")},
            http_status=409,
        )
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
    if audit_writer is not None:
        await audit_writer(session)  # 同步审计:与出金同事务,写失败即回滚不出金
    await session.commit()
    logger.info("refund_paid", refund_no=req.refund_no, channel=channel)
    return req


async def cancel_refund(session: AsyncSession, refund_id: int) -> RefundRequest:
    """取消(仅 pending/approved;已打款的终态不可取消)。不动钱包。"""
    req = await _get_for_update(session, refund_id)
    if req.status not in ("pending", "approved"):
        raise AppError(
            ErrorCode.CONFLICT,
            key="billing.refundStateNotCancellable",
            params={"status": req.status},
            http_status=409,
        )
    req.status = "cancelled"
    await session.commit()
    logger.info("refund_cancelled", refund_no=req.refund_no)
    return req
