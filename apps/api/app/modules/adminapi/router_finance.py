"""管理端路由(对账/告警/调账/退款/发票/订单/收入/补单)。"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import set_audit_target, write_audit_sync
from app.core.db import DbSession
from app.core.http import mark_idempotent_replay
from app.core.pagination import Page
from app.core.params import TzOffset
from app.modules.adminapi import export as admin_export
from app.modules.adminapi import service
from app.modules.adminapi.deps import CurrentAdmin, require_roles
from app.modules.adminapi.models import AdminUser
from app.modules.adminapi.router_shared import ExportLang, csv_response, parse_day
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
    ReconciliationOut,
    RevenueReportOut,
)
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

router = APIRouter(tags=["admin"])


# ---------- 财务对账(角色:finance / admin) ----------


@router.get("/reconciliation", dependencies=[require_roles("finance", "readonly")])
async def reconciliation(session: DbSession, day: str) -> ReconciliationOut:
    """日对账:事件计费 vs 指标估算 + diff%(>2% 列差异实例)。"""
    day_start, _ = parse_day(day)
    report = await metering_service.reconciliation_report(session, day_start)
    return ReconciliationOut.model_validate(report)


@router.get(
    "/reconciliation/export",
    dependencies=[require_roles("finance", "readonly")],
    responses={
        200: {"description": "CSV 导出", "content": {"text/csv": {"schema": {"type": "string"}}}}
    },
)
async def reconciliation_export(
    session: DbSession, day: str, lang: Literal["zh-CN", "en-US"] = ExportLang
) -> StreamingResponse:
    """日对账 CSV:与 GET /reconciliation 同一报告(首行合计 + diff 超阈实例明细)。"""
    day_start, _ = parse_day(day)
    report = await metering_service.reconciliation_report(session, day_start)
    return csv_response(
        admin_export.stream_reconciliation_csv(report, lang=lang),
        f"superdl-reconciliation-{day}.csv",
    )


def _alert_out(r: Any, usernames: dict[int, str]) -> AdminAlertOut:
    from app.modules.notify import service as notify_service

    kind, target_id = notify_service.alert_link_target(r)
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
        target_kind=kind,
        target_id=target_id,
    )


async def _ack_usernames(session: AsyncSession, rows: list[Any]) -> dict[int, str]:
    """确认人 id → 用户名(管理端回显「由谁确认」)。"""
    from sqlalchemy import select as sa_select

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
async def admin_alerts(session: DbSession, severity: str | None = None) -> list[AdminAlertOut]:
    """管理端告警流(总览右栏数据源)。severity 精确过滤(可选)。"""
    from app.modules.notify import service as notify_service

    rows = await notify_service.admin_alert_stream(session, severity=severity)
    usernames = await _ack_usernames(session, rows)
    return [_alert_out(r, usernames) for r in rows]


@router.get("/alerts/unread-count", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_alerts_unread_count(session: DbSession) -> AlertUnreadCountOut:
    """未确认告警计数(顶栏铃铛角标;独立计数端点)。"""
    from app.modules.notify import service as notify_service

    return AlertUnreadCountOut(count=await notify_service.unread_alert_count(session))


@router.post("/alerts/{alert_id}/ack", dependencies=[require_roles("ops")])
async def admin_ack_alert(
    alert_id: int, admin: CurrentAdmin, session: DbSession, request: Request
) -> AdminAlertOut:
    """确认告警(角色:ops/admin):落确认人与时间;重复确认 409。"""
    from app.modules.notify import service as notify_service

    row = await notify_service.ack_admin_alert(session, alert_id, acked_by=admin.id)
    set_audit_target(request, f"alert:{alert_id}")
    usernames = await _ack_usernames(session, [row])
    return _alert_out(row, usernames)


# ---------- 调账(发起:finance;复核:另一名 finance/admin) ----------


class AdjustmentCreate(BaseModel):
    user_id: int
    # 带符号金额字符串:严格十进制(禁科学计数法/前导零填充歧义/超 2 位小数),
    # 契约层挡下 Decimal("1e2") 这类合法但非预期的解析
    # (pydantic pattern 是 search 语义,必须自带 ^$ 锚)
    amount: str = Field(pattern=r"^-?(0|[1-9]\d{0,11})(\.\d{1,2})?$")
    reason: str = Field(min_length=2, max_length=256)


class AdjustmentReview(BaseModel):
    approve: bool
    comment: str | None = Field(default=None, max_length=256)


@router.get("/adjustments", dependencies=[require_roles("finance", "readonly")])
async def admin_list_adjustments(
    session: DbSession,
    status: str | None = None,
    user_id: int | None = None,
    day: str | None = None,
    cursor: str | None = None,
    limit: int | None = Query(default=None, le=100),
) -> Page[AdjustmentOut]:
    """调账单列表(游标分页,降序)。status/user_id 精确过滤;day=YYYY-MM-DD 按发起日过滤。"""
    return await service.list_adjustments(
        session,
        status=status,
        user_id=user_id,
        day_range=parse_day(day) if day else None,
        cursor=cursor,
        limit=limit,
    )


@router.post("/adjustments", status_code=201)
async def admin_create_adjustment(
    body: AdjustmentCreate,
    session: DbSession,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    admin: AdminUser = require_roles("finance"),
) -> AdjustmentStatusOut:
    """发起调账(双人复核前置)。支持 Idempotency-Key:重放返回已受理的单
    (200 + X-Idempotent-Replay)。"""
    adj, created = await service.create_adjustment(
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
    """复核调账(approve 即生效):审计行与生效同事务(write_audit_sync)。"""
    set_audit_target(request, f"adjustment:{adjustment_id}", detail={"approve": body.approve})
    adj = await service.review_adjustment(
        session,
        adjustment_id,
        approve=body.approve,
        reviewer_id=admin.id,
        comment=body.comment,
        audit_writer=lambda s: write_audit_sync(request, s),
    )
    return AdjustmentStatusOut(id=adj.id, status=adj.status)


# ---------- 退款(审批/打款双人制衡;角色:finance / admin) ----------


@router.get("/refunds", dependencies=[require_roles("finance", "readonly")])
async def admin_list_refunds(
    session: DbSession,
    status: str | None = None,
    day: str | None = None,
    cursor: str | None = None,
    limit: int | None = Query(default=None, le=100),
) -> Page[AdminRefundOut]:
    """退款单列表(游标分页,降序)。day=YYYY-MM-DD 按申请时间过滤。"""
    from app.modules.billing import service as billing_service

    return await billing_service.admin_list_refunds(
        session, status, day_range=parse_day(day) if day else None, cursor=cursor, limit=limit
    )


@router.post("/refunds/{refund_id}/review")
async def admin_review_refund(
    refund_id: int,
    body: RefundReview,
    session: DbSession,
    request: Request,
    admin: AdminUser = require_roles("finance"),
) -> AdminRefundOut:
    """审批(同意/驳回都须填意见)。通过 ≠ 出金:仅置 approved,等另一位财务登记打款。"""
    from app.modules.billing import service as billing_service

    req = await billing_service.review_refund(
        session, refund_id, approve=body.approve, comment=body.comment, reviewer_id=admin.id
    )
    set_audit_target(
        request,
        f"refund:{req.id}",
        detail={"refund_no": req.refund_no, "approve": body.approve, "comment": body.comment},
    )
    return AdminRefundOut.model_validate(req)


@router.post("/refunds/{refund_id}/payout")
async def admin_payout_refund(
    refund_id: int,
    body: RefundPayout,
    session: DbSession,
    request: Request,
    admin: AdminUser = require_roles("finance"),
) -> AdminRefundOut:
    """登记打款(唯一出金点):强制双人(与审批人相同则 409);余额不足 409,可取消。
    审计行与出金同事务(write_audit_sync):审计写失败即出金失败回滚。"""
    from app.modules.billing import service as billing_service

    set_audit_target(
        request,
        f"refund:{refund_id}",
        detail={"channel": body.channel, "ref": body.ref},
    )
    req = await billing_service.payout_refund(
        session,
        refund_id,
        channel=body.channel,
        ref=body.ref,
        operator_id=admin.id,
        audit_writer=lambda s: write_audit_sync(request, s),
    )
    return AdminRefundOut.model_validate(req)


@router.post("/refunds/{refund_id}/cancel")
async def admin_cancel_refund(
    refund_id: int,
    body: RefundCancel,
    session: DbSession,
    request: Request,
    admin: AdminUser = require_roles("finance"),
) -> AdminRefundOut:
    """取消退款单(仅 pending/approved;余额不足无法核销时的出口)。不动钱包。"""
    from app.modules.billing import service as billing_service

    req = await billing_service.cancel_refund(session, refund_id)
    set_audit_target(
        request, f"refund:{req.id}", detail={"refund_no": req.refund_no, "reason": body.reason}
    )
    return AdminRefundOut.model_validate(req)


# ---------- 发票(人工开票;读 ops/finance/readonly,写 finance/admin) ----------


@router.get("/invoices", dependencies=[require_roles("ops", "finance", "readonly")])
async def admin_list_invoices(
    session: DbSession, status: str | None = None, period: str | None = None
) -> list[AdminInvoiceOut]:
    """发票申请列表(固定截断 200)。status/period(YYYY-MM)精确过滤。"""
    from app.modules.billing import service as billing_service

    return await billing_service.admin_list_invoices(session, status=status, period=period)


@router.post("/invoices/{invoice_id}/issue")
async def admin_issue_invoice(
    invoice_id: int,
    body: InvoiceIssue,
    session: DbSession,
    request: Request,
    admin: AdminUser = require_roles("finance"),
) -> AdminInvoiceOut:
    """开票:回填发票号(人工开票,发票经邮箱送达),站内信告知用户。"""
    from app.modules.billing import service as billing_service

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
    """驳回(理由必填):站内信告知用户;驳回后同账期可重新申请。"""
    from app.modules.billing import service as billing_service

    req = await billing_service.reject_invoice(
        session, invoice_id, reason=body.reason, operator_id=admin.id
    )
    set_audit_target(
        request, f"invoice:{req.id}", detail={"period": req.period, "reason": body.reason}
    )
    return AdminInvoiceOut.model_validate(req)


# ---------- 财务流水(角色:finance) ----------


@router.get("/orders", dependencies=[require_roles("finance", "readonly")])
async def admin_list_orders(
    session: DbSession,
    status: str | None = None,
    order_no: str | None = None,
    user_id: int | None = None,
    day: str | None = None,
    cursor: str | None = None,
    limit: int | None = Query(default=None, le=100),
) -> Page[AdminOrderOut]:
    """充值订单列表(游标分页,降序)。order_no 精确匹配,是 verify / backfill 两个补救端点的
    入参来源;day=YYYY-MM-DD 按下单日过滤(UTC 日,与对账口径一致)。"""
    from app.modules.billing import service as billing_service

    page = await billing_service.admin_list_orders(
        session,
        status,
        order_no=order_no,
        user_id=user_id,
        day_range=parse_day(day) if day else None,
        cursor=cursor,
        limit=limit,
    )
    return Page[AdminOrderOut](
        items=[AdminOrderOut.model_validate(r) for r in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/orders/export",
    dependencies=[require_roles("finance", "readonly")],
    responses={
        200: {"description": "CSV 导出", "content": {"text/csv": {"schema": {"type": "string"}}}}
    },
)
async def admin_orders_export(
    session: DbSession,
    status: str | None = None,
    order_no: str | None = None,
    user_id: int | None = None,
    day: str | None = None,
    tz_offset_minutes: int = TzOffset,
    lang: Literal["zh-CN", "en-US"] = ExportLang,
) -> StreamingResponse:
    """充值订单 CSV(流式):筛选口径与 GET /orders 一致;行数硬上限 + 截断标记行。"""
    from app.modules.billing import service as billing_service

    return csv_response(
        billing_service.stream_admin_orders_csv(
            session,
            status=status,
            order_no=order_no,
            user_id=user_id,
            day_range=parse_day(day) if day else None,
            tz_offset_minutes=tz_offset_minutes,
            lang=lang,
        ),
        f"superdl-orders-{day or 'all'}.csv",
    )


# ---------- 收入报表(总览 KPI) ----------


@router.get("/reports/revenue", dependencies=[require_roles("ops", "finance", "readonly")])
async def revenue_report(session: DbSession, tz_offset_minutes: int = TzOffset) -> RevenueReportOut:
    """今日/本月消费额(营收口径 = ledger consume 绝对值)与新注册数。本地日界经 tz_offset。"""
    from app.modules.account import service as account_service
    from app.modules.billing import service as billing_service

    revenue = await billing_service.revenue_summary(session, tz_offset_minutes=tz_offset_minutes)
    signups = await account_service.signup_counts(session, tz_offset_minutes=tz_offset_minutes)
    return RevenueReportOut.model_validate({**revenue, **signups})


@router.get("/finance/anomalies", dependencies=[require_roles("finance", "readonly")])
async def admin_payment_anomalies(session: DbSession) -> list[PaymentAnomalyOut]:
    """异常清单:疑似丢回调 / 近 48h 关单 / 负余额钱包。"""
    from app.modules.billing import service as billing_service

    rows = await billing_service.list_payment_anomalies(session)
    return [PaymentAnomalyOut.model_validate(r) for r in rows]


@router.post("/finance/orders/{order_no}/verify", dependencies=[require_roles("finance")])
async def admin_verify_order(order_no: str, session: DbSession, request: Request) -> OrderVerifyOut:
    """向渠道核验订单状态与金额(补单前置;渠道结果是唯一事实源)。"""
    from app.modules.billing import service as billing_service

    result = await billing_service.verify_order(session, order_no)
    set_audit_target(
        request, f"order:{order_no}", detail={"channel_status": result["channel_status"]}
    )
    return OrderVerifyOut.model_validate(result)


class OrderBackfillRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=REASON_MAX_LENGTH)


@router.post("/finance/orders/{order_no}/backfill", dependencies=[require_roles("finance")])
async def admin_backfill_order(
    order_no: str,
    body: OrderBackfillRequest,
    session: DbSession,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> OrderBackfillOut:
    """人工补单:服务端实时向渠道核验已支付且金额一致才入账。同幂等键重放回当前状态
    (X-Idempotent-Replay 头区分)。审计行与入账同事务(write_audit_sync)。"""
    from app.modules.billing import service as billing_service

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


# ---------- 结算缺口(水位线被越过但账未结清的窗口留痕;角色:finance 读/写) ----------


@router.get("/finance/settlement-gaps", dependencies=[require_roles("finance", "readonly")])
async def admin_list_settlement_gaps(
    session: DbSession,
    kind: Literal["hourly", "daily_disk"] | None = None,
    reason: str | None = None,
    unresolved: bool = True,
    cursor: str | None = None,
    limit: int | None = Query(default=None, le=100),
) -> Page[AdminSettlementGapOut]:
    """缺口列表(游标分页,降序):默认只看未核销——缺口闭环前需要持续曝光,
    配套持续告警 superdl_settlement_gap_unresolved(DB 口径)。"""
    from app.modules.billing import service as billing_service

    return await billing_service.admin_list_gaps(
        session, kind=kind, reason=reason, unresolved_only=unresolved, cursor=cursor, limit=limit
    )


@router.post("/finance/settlement-gaps/{gap_id}/replay")
async def admin_replay_settlement_gap(
    gap_id: int,
    request: Request,
    admin: AdminUser = require_roles("finance"),
) -> AdminSettlementGapOut:
    """重放缺口窗口的幂等入账原语(人工触发,不自动改账):成功回写 resolved_at。
    grace_overlap 缺口拒重放(409,走人工核销);对象已不存在 409(同样走人工核销)。"""
    from app.core.db import get_sessionmaker
    from app.modules.billing import service as billing_service

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
    """人工核销(不重放):对象已不存在/grace_overlap 确认无账时的出口。说明必填。"""
    from app.modules.billing import service as billing_service

    gap = await billing_service.resolve_gap(session, gap_id, note=body.note, operator_id=admin.id)
    set_audit_target(
        request, f"settlement_gap:{gap_id}", detail={"action": "resolve", "note": body.note}
    )
    return AdminSettlementGapOut.model_validate(gap)
