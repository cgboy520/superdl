"""发票闭环:用户按账期申请 → 财务人工开票(填发票号)/驳回 → 站内信告知。

关键不变量:
- 金额只由服务端计算:某账期可开票额 = Σ(该账期 paid 充值订单,不含渠道冲正)
  − Σ(该账期订单的退款:已打款 + 在途 pending/approved,一律按关联订单 paid_at 归属,不按打款时间)
  − Σ(该账期 submitted + issued 申请)。客户端只提交账期与抬头,提交金额无效。
- 按账期合并开具,一个自然月一张;仅可申请 < 当前北京月的账期(本月 paid 订单
  还可能变,不当月开票)。
- 同一 (user_id, period) 仅一条非 rejected 申请(部分唯一索引兜底并发);
  rejected 后同账期可重新申请。
- 与退款联动(两道闸):
  已开票(issued)账期的 paid 订单不可申请退款,须先红冲(refunds._order_has_issued_invoice,
  申请时对该账期的活跃申请行 FOR UPDATE,与本模块 issue_invoice 的行锁串行);
  开票(issue_invoice)行锁内按当前口径重算金额(在途退款已预扣),申请到开票之间发生退款
  即 409 驳回重申。登记打款不再复查账期是否已开票:能走到打款的退款早已从票额里扣掉,
  复查只会把它打成死胡同(只能取消,再申请又被已开票拦下)。
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode, not_found
from app.core.logging import get_logger
from app.core.money import as_amount
from app.core.pagination import Page, clamp_limit, decode_cursor_int, slice_page
from app.core.timeutil import BILLING_DAY_OFFSET, now_utc
from app.modules.billing.models import InvoiceRequest, Order, RefundRequest
from app.modules.billing.schemas import AdminInvoiceOut, InvoiceEligibleOut, InvoiceOut
from app.modules.notify import service as notify_service

logger = get_logger(__name__)

ACTIVE_STATUSES = ("submitted", "issued")
# 从可开票额扣除的退款状态:已打款 + 在途(pending/approved);rejected/cancelled 不扣
REFUND_WITHHELD_STATUSES = ("pending", "approved", "paid")

# 管理端列表固定截断,与 admin/components/ListCapNote.tsx 的 LIST_CAPS.invoices 对齐
ADMIN_LIST_CAP = 200


def beijing_period(dt: datetime) -> str:
    """UTC 时刻所属的北京账期(YYYY-MM)。北京无夏令时,固定 +8 偏移。"""
    return f"{dt + BILLING_DAY_OFFSET:%Y-%m}"


def current_beijing_period() -> str:
    return beijing_period(now_utc())


def _period_range_utc(period: str) -> tuple[datetime, datetime]:
    """账期(北京自然月)对应的 UTC [start, end) 窗口。period 已按契约 YYYY-MM 校验。"""
    local_start = datetime.strptime(period, "%Y-%m").replace(tzinfo=UTC)
    local_end = (
        local_start.replace(year=local_start.year + 1, month=1)
        if local_start.month == 12
        else local_start.replace(month=local_start.month + 1)
    )
    return local_start - BILLING_DAY_OFFSET, local_end - BILLING_DAY_OFFSET


async def _period_paid_sum(session: AsyncSession, user_id: int, period: str) -> Decimal:
    """该用户该账期(北京月界)已支付充值订单总额(不含已被渠道冲正的订单——
    冲正意味着钱已被渠道划回,对其开票等于为未收到的款纳税)。"""
    start, end = _period_range_utc(period)
    total = (
        await session.execute(
            select(func.coalesce(func.sum(Order.amount), 0)).where(
                Order.user_id == user_id,
                Order.type == "recharge",
                Order.status == "paid",
                Order.channel_reversed_at.is_(None),
                Order.paid_at >= start,
                Order.paid_at < end,
            )
        )
    ).scalar_one()
    return Decimal(total)


async def _period_refund_sum(session: AsyncSession, user_id: int, period: str) -> Decimal:
    """该账期订单的退款总额(已打款 + 在途 pending/approved),按关联订单的支付账期归属。

    开票口径为净实收:当期已退的款不能再开票,在途退款也预扣——否则「先申请退款 →
    再申请发票 → 开票 → 打款」时序下发票与退款双重兑现。归属一律看订单 paid_at 而非
    打款时间:按打款时间归属会让退款在 pending→paid 跨月时从订单账期挪走,订单账期
    重算即可全额开票,打款账期又被扣一次。
    """
    start, end = _period_range_utc(period)
    total = (
        await session.execute(
            select(func.coalesce(func.sum(RefundRequest.amount), 0))
            .join(Order, RefundRequest.order_no == Order.order_no)
            .where(
                RefundRequest.user_id == user_id,
                RefundRequest.status.in_(REFUND_WITHHELD_STATUSES),
                Order.paid_at >= start,
                Order.paid_at < end,
            )
        )
    ).scalar_one()
    return Decimal(total)


async def _period_active_sum(session: AsyncSession, user_id: int, period: str) -> Decimal:
    """该账期已占用额度:申请中(submitted)+ 已开票(issued)申请金额合计。"""
    total = (
        await session.execute(
            select(func.coalesce(func.sum(InvoiceRequest.amount), 0)).where(
                InvoiceRequest.user_id == user_id,
                InvoiceRequest.period == period,
                InvoiceRequest.status.in_(ACTIVE_STATUSES),
            )
        )
    ).scalar_one()
    return Decimal(total)


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


async def _get_by_idempotency_key(
    session: AsyncSession, user_id: int, idempotency_key: str
) -> InvoiceRequest | None:
    return (
        await session.execute(
            select(InvoiceRequest).where(
                InvoiceRequest.user_id == user_id,
                InvoiceRequest.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()


async def eligible_periods(session: AsyncSession, user_id: int) -> list[InvoiceEligibleOut]:
    """各账期可开票额度预览:Σpaid − Σ(submitted+issued),仅返回 > 0 且已结束的账期。"""
    # 按北京月分组:timezone() 显式指定 Asia/Shanghai(=固定 +8,无夏令时),
    # 不受会话 TimeZone 设置影响
    period_col = func.to_char(func.timezone("Asia/Shanghai", Order.paid_at), "YYYY-MM")
    paid_rows = (
        (
            await session.execute(
                select(period_col, func.sum(Order.amount))
                .where(
                    Order.user_id == user_id,
                    Order.type == "recharge",
                    Order.status == "paid",
                    Order.channel_reversed_at.is_(None),  # 同 _period_paid_sum:冲正单不可开
                    Order.paid_at.is_not(None),
                )
                .group_by(period_col)
            )
        )
        .tuples()
        .all()
    )
    active_rows = (
        (
            await session.execute(
                select(InvoiceRequest.period, func.sum(InvoiceRequest.amount))
                .where(
                    InvoiceRequest.user_id == user_id,
                    InvoiceRequest.status.in_(ACTIVE_STATUSES),
                )
                .group_by(InvoiceRequest.period)
            )
        )
        .tuples()
        .all()
    )
    # 退款(已打款 + 在途)按关联订单 paid_at 归账期扣除(与 _period_refund_sum 同口径,预览=申请)
    refund_rows = (
        (
            await session.execute(
                select(period_col, func.sum(RefundRequest.amount))
                .join(Order, RefundRequest.order_no == Order.order_no)
                .where(
                    RefundRequest.user_id == user_id,
                    RefundRequest.status.in_(REFUND_WITHHELD_STATUSES),
                    Order.paid_at.is_not(None),
                )
                .group_by(period_col)
            )
        )
        .tuples()
        .all()
    )
    active_map = {p: Decimal(a) for p, a in active_rows}
    refund_map = {p: Decimal(a) for p, a in refund_rows}
    current = current_beijing_period()
    out: list[InvoiceEligibleOut] = []
    for period, paid_sum in paid_rows:
        if period >= current:
            continue  # 当月账期不可开:paid 订单还可能变
        remaining = as_amount(
            Decimal(paid_sum)
            - refund_map.get(period, Decimal("0"))
            - active_map.get(period, Decimal("0"))
        )
        if remaining > 0:
            out.append(InvoiceEligibleOut(period=period, amount=remaining))
    out.sort(key=lambda item: item.period, reverse=True)
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
    """申请开票。幂等:Idempotency-Key 重放返回既有单(唯一约束兜底并发)。
    返回 (申请单, created):created=False = 幂等重放,路由回 200 + X-Idempotent-Replay。"""
    if idempotency_key:
        existing = await _get_by_idempotency_key(session, user_id, idempotency_key)
        if existing is not None:
            return existing, False  # 幂等重放

    if period >= current_beijing_period():
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="billing.invoicePeriodNotOpen",
            params={"period": period},
        )
    if await _active_of_period(session, user_id, period) is not None:
        raise AppError(
            ErrorCode.CONFLICT,
            key="billing.invoicePeriodAlreadyApplied",
            params={"period": period},
            http_status=409,
        )
    # 金额服务端计算(客户端提交金额无效):
    # Σpaid − Σ退款(已打款 + 在途,防票款双重兑现) − Σ(submitted+issued)
    paid = await _period_paid_sum(session, user_id, period)
    refunded = await _period_refund_sum(session, user_id, period)
    amount = as_amount(paid - refunded - await _period_active_sum(session, user_id, period))
    if amount <= 0:
        raise AppError(
            ErrorCode.CONFLICT,
            key="billing.invoiceNothingToBill",
            params={"period": period},
            http_status=409,
        )

    req = InvoiceRequest(
        user_id=user_id,
        period=period,
        title_type=title_type,
        title=title,
        tax_id=tax_id,
        email=email,
        amount=amount,
        idempotency_key=idempotency_key,
    )
    session.add(req)
    try:
        await session.commit()
        logger.info("invoice_created", invoice_id=req.id, user_id=user_id, period=period)
        return req, True
    except IntegrityError:
        await session.rollback()
        if idempotency_key:
            winner = await _get_by_idempotency_key(session, user_id, idempotency_key)
            if winner is not None:
                return winner, False  # 同键并发:返回胜出方的单
        # 撞的是部分唯一索引(并发重复申请同一账期)
        raise AppError(
            ErrorCode.CONFLICT,
            key="billing.invoicePeriodAlreadyApplied",
            params={"period": period},
            http_status=409,
        ) from None


async def list_my_invoices(
    session: AsyncSession, user_id: int, *, cursor: str | None = None, limit: int | None = None
) -> Page[InvoiceOut]:
    """本人发票申请(游标分页,语义与退款单/资金流水一致)。"""
    lim = clamp_limit(limit)
    stmt = (
        select(InvoiceRequest)
        .where(InvoiceRequest.user_id == user_id)
        .order_by(InvoiceRequest.id.desc())
        .limit(lim + 1)
    )
    last_id = decode_cursor_int(cursor)
    if last_id is not None:
        stmt = stmt.where(InvoiceRequest.id < last_id)
    rows = list((await session.execute(stmt)).scalars())
    page_items, next_cursor = slice_page(rows, lim, key=lambda r: r.id)
    return Page[InvoiceOut](
        items=[InvoiceOut.model_validate(r) for r in page_items], next_cursor=next_cursor
    )


# ---------- 管理端(finance/admin 写,ops/finance/readonly 读) ----------


async def admin_list_invoices(
    session: AsyncSession, status: str | None = None, period: str | None = None
) -> list[AdminInvoiceOut]:
    """发票申请列表(固定截断)。status/period 精确过滤。"""
    stmt = select(InvoiceRequest).order_by(InvoiceRequest.id.desc()).limit(ADMIN_LIST_CAP)
    if status:
        stmt = stmt.where(InvoiceRequest.status == status)
    if period:
        stmt = stmt.where(InvoiceRequest.period == period)
    rows = (await session.execute(stmt)).scalars()
    return [AdminInvoiceOut.model_validate(r) for r in rows]


async def _get_for_update(session: AsyncSession, invoice_id: int) -> InvoiceRequest:
    req = await session.get(InvoiceRequest, invoice_id, with_for_update=True)
    if req is None:
        raise not_found(key="billing.invoiceNotFound")
    return req


async def issue_invoice(
    session: AsyncSession, invoice_id: int, *, invoice_no: str, operator_id: int
) -> InvoiceRequest:
    """开票(行锁内状态迁移):金额重算闸 + 回填发票号 + 操作人,站内信告知用户。"""
    req = await _get_for_update(session, invoice_id)
    if req.status != "submitted":
        raise AppError(
            ErrorCode.CONFLICT,
            key="billing.invoiceStateNotIssuable",
            params={"status": req.status},
            http_status=409,
        )
    # 行锁内按当前口径重算:申请到开票之间若发生退款(申请/打款),可开票额已变,
    # 按旧额开票后用户再拿退款 = 票款双重兑现;不符即 409,驳回由用户按新额重新申请
    # (create_refund 对本行 FOR UPDATE:在途退款要么已计入本次重算,要么在锁后看到 issued 被拒)
    paid = await _period_paid_sum(session, req.user_id, req.period)
    refunded = await _period_refund_sum(session, req.user_id, req.period)
    active = await _period_active_sum(session, req.user_id, req.period)
    current = as_amount(paid - refunded - (active - req.amount))
    if current != req.amount:
        raise AppError(
            ErrorCode.CONFLICT,
            key="billing.invoiceAmountStale",
            params={"expected": format(current, "f"), "requested": format(req.amount, "f")},
            http_status=409,
        )
    req.status = "issued"
    req.invoice_no = invoice_no
    req.issued_by = operator_id
    req.issued_at = now_utc()
    await notify_service.notify(
        session,
        req.user_id,
        type_="invoice",
        title="发票已开具",
        content=(
            f"您 {req.period} 账期的发票(金额 ¥{format(req.amount, 'f')})已开具,"
            f"发票号 {invoice_no},将于 1-3 个工作日内发送至您的邮箱 {req.email}。"
        ),
        dedup_key=f"invoice:issued:{req.id}",
    )
    await session.commit()
    logger.info("invoice_issued", invoice_id=req.id, invoice_no=invoice_no)
    return req


async def reject_invoice(
    session: AsyncSession, invoice_id: int, *, reason: str, operator_id: int
) -> InvoiceRequest:
    """驳回(行锁内状态迁移):理由必填,站内信告知用户;驳回后同账期可重新申请。"""
    req = await _get_for_update(session, invoice_id)
    if req.status != "submitted":
        raise AppError(
            ErrorCode.CONFLICT,
            key="billing.invoiceStateNotRejectable",
            params={"status": req.status},
            http_status=409,
        )
    req.status = "rejected"
    req.reject_reason = reason
    await notify_service.notify(
        session,
        req.user_id,
        type_="invoice",
        title="发票申请被驳回",
        content=f"您 {req.period} 账期的开票申请被驳回:{reason}。可修改抬头信息后重新提交。",
        dedup_key=f"invoice:rejected:{req.id}",
    )
    await session.commit()
    logger.info("invoice_rejected", invoice_id=req.id, operator_id=operator_id)
    return req
