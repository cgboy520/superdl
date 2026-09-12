"""账单 CSV 导出:流式生成,单响应行数硬上限 + 截断标记行。

转义/时区/上限/流式骨架在 app.core.csvexport(管理端导出共用);本文件只留业务表查询与列定义。
"""

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.csvexport import TRUNCATED_NOTES, fmt_money, fmt_ts, stream_rows
from app.core.timeutil import BILLING_TZ_OFFSET_MINUTES
from app.modules.account import service as account_service
from app.modules.billing.models import (
    BalanceLedger,
    BillHourly,
    InvoiceRequest,
    Order,
    RefundRequest,
)

_HEADERS: dict[tuple[str, str], list[str]] = {
    ("hourly", "zh-CN"): ["小时", "实例ID", "运行秒数", "单价(元/时)", "卡数", "金额(元)"],
    ("hourly", "en-US"): [
        "Hour",
        "Instance ID",
        "Seconds",
        "Unit price (CNY/hr)",
        "GPUs",
        "Amount (CNY)",
    ],
    ("ledger", "zh-CN"): ["时间", "类型", "金额(元)", "余额快照(元)", "关联", "备注"],
    ("ledger", "en-US"): ["Time", "Type", "Amount (CNY)", "Balance after (CNY)", "Ref", "Remark"],
    ("admin_orders", "zh-CN"): [
        "订单号",
        "租户ID",
        "金额(元)",
        "渠道",
        "状态",
        "支付时间",
        "创建时间",
    ],
    ("admin_orders", "en-US"): [
        "Order no",
        "Tenant ID",
        "Amount (CNY)",
        "Channel",
        "Status",
        "Paid at",
        "Created at",
    ],
    ("admin_refunds", "zh-CN"): [
        "退款单号",
        "用户ID",
        "订单号",
        "金额(元)",
        "状态",
        "审批人",
        "打款渠道",
        "凭证号",
        "申请时间",
    ],
    ("admin_refunds", "en-US"): [
        "Refund no",
        "User ID",
        "Order no",
        "Amount (CNY)",
        "Status",
        "Reviewer",
        "Payout channel",
        "Payout ref",
        "Applied at",
    ],
    ("admin_invoices", "zh-CN"): [
        "发票号",
        "用户ID",
        "账期",
        "金额(元)",
        "抬头",
        "税号",
        "状态",
        "邮箱",
        "申请时间",
    ],
    ("admin_invoices", "en-US"): [
        "Invoice no",
        "User ID",
        "Period",
        "Amount (CNY)",
        "Title",
        "Tax ID",
        "Status",
        "Email",
        "Applied at",
    ],
}

_LEDGER_TYPE_LABEL: dict[str, dict[str, str]] = {
    "zh-CN": {"recharge": "充值", "consume": "消费", "refund": "退款", "adjust": "调账"},
    "en-US": {
        "recharge": "Recharge",
        "consume": "Consumption",
        "refund": "Refund",
        "adjust": "Adjustment",
    },
}

_ORDER_STATUS_LABEL: dict[str, dict[str, str]] = {
    "zh-CN": {"pending": "待支付", "paid": "已支付", "closed": "已关闭", "failed": "失败"},
    "en-US": {"pending": "Pending", "paid": "Paid", "closed": "Closed", "failed": "Failed"},
}

_ORDER_CHANNEL_LABEL: dict[str, dict[str, str]] = {
    "zh-CN": {"wechat": "微信支付", "alipay": "支付宝", "mock": "模拟渠道"},
    "en-US": {"wechat": "WeChat Pay", "alipay": "Alipay", "mock": "Mock"},
}

# 状态/渠道文案与 packages/ui shared.json 同一口径
_REFUND_STATUS_LABEL: dict[str, dict[str, str]] = {
    "zh-CN": {
        "pending": "待审批",
        "approved": "待打款",
        "rejected": "已驳回",
        "paid": "已完成",
        "cancelled": "已取消",
    },
    "en-US": {
        "pending": "Pending review",
        "approved": "Awaiting payout",
        "rejected": "Rejected",
        "paid": "Paid",
        "cancelled": "Cancelled",
    },
}

_PAYOUT_CHANNEL_LABEL: dict[str, dict[str, str]] = {
    "zh-CN": {
        "offline": "线下转账",
        "alipay_transfer": "支付宝转账",
        "wechat_transfer": "微信转账",
    },
    "en-US": {
        "offline": "Offline transfer",
        "alipay_transfer": "Alipay transfer",
        "wechat_transfer": "WeChat transfer",
    },
}

_INVOICE_STATUS_LABEL: dict[str, dict[str, str]] = {
    "zh-CN": {"submitted": "审核中", "issued": "已开票", "rejected": "已驳回"},
    "en-US": {"submitted": "In review", "issued": "Issued", "rejected": "Rejected"},
}


def stream_hourly_csv(
    session: AsyncSession,
    user_id: int,
    *,
    month_range: tuple[datetime, datetime] | None = None,
    tz_offset_minutes: int = BILLING_TZ_OFFSET_MINUTES,
    lang: str = "zh-CN",
) -> AsyncIterator[str]:
    """小时账单 CSV(降序,最新在前);month_range 为 [起, 讫) UTC 窗口。"""
    stmt = select(BillHourly).where(BillHourly.user_id == user_id)
    if month_range is not None:
        stmt = stmt.where(
            BillHourly.hour_start >= month_range[0], BillHourly.hour_start < month_range[1]
        )

    def row(r: BillHourly) -> list[object]:
        return [
            fmt_ts(r.hour_start, tz_offset_minutes),
            r.instance_id,
            r.seconds_used,
            fmt_money(r.unit_price),
            r.gpu_count,
            fmt_money(r.amount),
        ]

    return stream_rows(
        session,
        stmt,
        BillHourly.id,
        row,
        _HEADERS[("hourly", lang)],
        truncated_note=TRUNCATED_NOTES[lang],
    )


def stream_ledger_csv(
    session: AsyncSession,
    user_id: int,
    *,
    tz_offset_minutes: int = BILLING_TZ_OFFSET_MINUTES,
    lang: str = "zh-CN",
) -> AsyncIterator[str]:
    """资金流水 CSV(降序,最新在前)。用户端与管理端租户下钻导出共用。"""
    labels = _LEDGER_TYPE_LABEL[lang]

    def row(r: BalanceLedger) -> list[object]:
        return [
            fmt_ts(r.created_at, tz_offset_minutes),
            labels.get(r.type, r.type),
            fmt_money(r.amount),
            fmt_money(r.balance_after),
            f"{r.ref_type}:{r.ref_id or ''}" if r.ref_type else "",
            r.remark or "",
        ]

    return stream_rows(
        session,
        select(BalanceLedger).where(BalanceLedger.user_id == user_id),
        BalanceLedger.id,
        row,
        _HEADERS[("ledger", lang)],
        truncated_note=TRUNCATED_NOTES[lang],
    )


def stream_admin_orders_csv(
    session: AsyncSession,
    *,
    status: str | None = None,
    order_no: str | None = None,
    user_id: int | None = None,
    day_range: tuple[datetime, datetime] | None = None,
    tz_offset_minutes: int = BILLING_TZ_OFFSET_MINUTES,
    lang: str = "zh-CN",
) -> AsyncIterator[str]:
    """管理端充值订单 CSV(降序;筛选口径与 GET /admin/v1/orders 一致)。"""
    stmt = select(Order)
    if status:
        stmt = stmt.where(Order.status == status)
    if order_no:
        stmt = stmt.where(Order.order_no == order_no.strip())
    if user_id:
        stmt = stmt.where(Order.user_id == user_id)
    if day_range is not None:
        stmt = stmt.where(Order.created_at >= day_range[0], Order.created_at < day_range[1])
    status_labels = _ORDER_STATUS_LABEL[lang]
    channel_labels = _ORDER_CHANNEL_LABEL[lang]

    def row(r: Order) -> list[object]:
        return [
            r.order_no,
            r.user_id,
            fmt_money(r.amount),
            channel_labels.get(r.channel, r.channel),
            status_labels.get(r.status, r.status),
            fmt_ts(r.paid_at, tz_offset_minutes) if r.paid_at else "",
            fmt_ts(r.created_at, tz_offset_minutes),
        ]

    return stream_rows(
        session,
        stmt,
        Order.id,
        row,
        _HEADERS[("admin_orders", lang)],
        truncated_note=TRUNCATED_NOTES[lang],
    )


def stream_admin_refunds_csv(
    session: AsyncSession,
    *,
    status: str | None = None,
    day_range: tuple[datetime, datetime] | None = None,
    tz_offset_minutes: int = BILLING_TZ_OFFSET_MINUTES,
    lang: str = "zh-CN",
) -> AsyncIterator[str]:
    """管理端退款单 CSV(降序;筛选口径与 GET /admin/v1/refunds 一致)。"""
    stmt = select(RefundRequest)
    if status:
        stmt = stmt.where(RefundRequest.status == status)
    if day_range is not None:
        stmt = stmt.where(
            RefundRequest.created_at >= day_range[0], RefundRequest.created_at < day_range[1]
        )
    status_labels = _REFUND_STATUS_LABEL[lang]
    channel_labels = _PAYOUT_CHANNEL_LABEL[lang]

    def row(r: RefundRequest) -> list[object]:
        return [
            r.refund_no,
            r.user_id,
            r.order_no,
            fmt_money(r.amount),
            status_labels.get(r.status, r.status),
            r.review_by if r.review_by is not None else "",
            (channel_labels.get(r.payout_channel, r.payout_channel) if r.payout_channel else ""),
            r.payout_ref or "",
            fmt_ts(r.created_at, tz_offset_minutes),
        ]

    return stream_rows(
        session,
        stmt,
        RefundRequest.id,
        row,
        _HEADERS[("admin_refunds", lang)],
        truncated_note=TRUNCATED_NOTES[lang],
    )


def mask_invoice_identity(value: str) -> str:
    """发票抬头/邮箱的默认脱敏(复用 mask_id_name:留首字符,其余打星)。"""
    return account_service.mask_id_name(value) if value else value


def stream_admin_invoices_csv(
    session: AsyncSession,
    *,
    status: str | None = None,
    period: str | None = None,
    tz_offset_minutes: int = BILLING_TZ_OFFSET_MINUTES,
    lang: str = "zh-CN",
    reveal: bool = False,
    row_counter: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    """管理端发票申请 CSV(降序;筛选口径与 GET /admin/v1/invoices 一致)。

    reveal=False(默认)脱敏抬头与邮箱;row_counter 由 row() 边吐边记实际行数,供调用方落审计。
    """
    stmt = select(InvoiceRequest)
    if status:
        stmt = stmt.where(InvoiceRequest.status == status)
    if period:
        stmt = stmt.where(InvoiceRequest.period == period)
    status_labels = _INVOICE_STATUS_LABEL[lang]

    def row(r: InvoiceRequest) -> list[object]:
        if row_counter is not None:
            row_counter["rows"] = int(row_counter.get("rows", 0)) + 1
        return [
            r.invoice_no or "",
            r.user_id,
            r.period,
            fmt_money(r.amount),
            r.title if reveal else mask_invoice_identity(r.title),
            r.tax_id or "",
            status_labels.get(r.status, r.status),
            r.email if reveal else mask_invoice_identity(r.email),
            fmt_ts(r.created_at, tz_offset_minutes),
        ]

    return stream_rows(
        session,
        stmt,
        InvoiceRequest.id,
        row,
        _HEADERS[("admin_invoices", lang)],
        truncated_note=TRUNCATED_NOTES[lang],
    )
