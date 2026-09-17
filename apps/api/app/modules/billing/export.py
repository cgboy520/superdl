"""Streamed billing CSV exports; rows are capped, a truncation marker row is written past the
cap."""

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.csvexport import TRUNCATED_NOTES, fmt_money, fmt_ts, header_row, stream_rows
from app.modules.account import service as account_service
from app.modules.billing import invoices, refunds, wallet
from app.modules.billing.models import (
    BalanceLedger,
    BillHourly,
    InvoiceRequest,
    Order,
    RefundRequest,
)

_HEADERS: dict[tuple[str, str], list[str]] = {
    ("hourly", "zh-CN"): [
        "小时",  # cjk-ok
        "实例ID",  # cjk-ok
        "运行秒数",  # cjk-ok
        "单价({currency}/时)",  # cjk-ok
        "卡数",  # cjk-ok
        "金额({currency})",  # cjk-ok
    ],
    ("hourly", "en-US"): [
        "Hour",
        "Instance ID",
        "Seconds",
        "Unit price ({currency}/hr)",
        "GPUs",
        "Amount ({currency})",
    ],
    ("ledger", "zh-CN"): [
        "时间",  # cjk-ok
        "类型",  # cjk-ok
        "金额({currency})",  # cjk-ok
        "余额快照({currency})",  # cjk-ok
        "关联",  # cjk-ok
        "备注",  # cjk-ok
    ],
    ("ledger", "en-US"): [
        "Time",
        "Type",
        "Amount ({currency})",
        "Balance after ({currency})",
        "Ref",
        "Remark",
    ],
    ("admin_orders", "zh-CN"): [
        "订单号",  # cjk-ok
        "租户ID",  # cjk-ok
        "金额({currency})",  # cjk-ok
        "渠道",  # cjk-ok
        "状态",  # cjk-ok
        "支付时间",  # cjk-ok
        "创建时间",  # cjk-ok
    ],
    ("admin_orders", "en-US"): [
        "Order no",
        "Tenant ID",
        "Amount ({currency})",
        "Channel",
        "Status",
        "Paid at",
        "Created at",
    ],
    ("admin_refunds", "zh-CN"): [
        "退款单号",  # cjk-ok
        "用户ID",  # cjk-ok
        "订单号",  # cjk-ok
        "金额({currency})",  # cjk-ok
        "状态",  # cjk-ok
        "审批人",  # cjk-ok
        "打款渠道",  # cjk-ok
        "凭证号",  # cjk-ok
        "申请时间",  # cjk-ok
    ],
    ("admin_refunds", "en-US"): [
        "Refund no",
        "User ID",
        "Order no",
        "Amount ({currency})",
        "Status",
        "Reviewer",
        "Payout channel",
        "Payout ref",
        "Applied at",
    ],
    ("admin_invoices", "zh-CN"): [
        "发票号",  # cjk-ok
        "用户ID",  # cjk-ok
        "账期",  # cjk-ok
        "金额({currency})",  # cjk-ok
        "抬头",  # cjk-ok
        "税号",  # cjk-ok
        "状态",  # cjk-ok
        "邮箱",  # cjk-ok
        "申请时间",  # cjk-ok
    ],
    ("admin_invoices", "en-US"): [
        "Invoice no",
        "User ID",
        "Period",
        "Amount ({currency})",
        "Title",
        "Tax ID",
        "Status",
        "Email",
        "Applied at",
    ],
}

_LEDGER_TYPE_LABEL: dict[str, dict[str, str]] = {
    "zh-CN": {"recharge": "充值", "consume": "消费", "refund": "退款", "adjust": "调账"},  # cjk-ok
    "en-US": {
        "recharge": "Recharge",
        "consume": "Consumption",
        "refund": "Refund",
        "adjust": "Adjustment",
    },
}

_ORDER_STATUS_LABEL: dict[str, dict[str, str]] = {
    "zh-CN": {
        "pending": "待支付",  # cjk-ok
        "paid": "已支付",  # cjk-ok
        "closed": "已关闭",  # cjk-ok
        "failed": "失败",  # cjk-ok
    },  # cjk-ok
    "en-US": {"pending": "Pending", "paid": "Paid", "closed": "Closed", "failed": "Failed"},
}

_ORDER_CHANNEL_LABEL: dict[str, dict[str, str]] = {
    "zh-CN": {"wechat": "微信支付", "alipay": "支付宝", "mock": "模拟渠道"},  # cjk-ok
    "en-US": {"wechat": "WeChat Pay", "alipay": "Alipay", "mock": "Mock"},
}

_REFUND_STATUS_LABEL: dict[str, dict[str, str]] = {
    "zh-CN": {
        "pending": "待审批",  # cjk-ok
        "approved": "待打款",  # cjk-ok
        "rejected": "已驳回",  # cjk-ok
        "paid": "已完成",  # cjk-ok
        "cancelled": "已取消",  # cjk-ok
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
        "offline": "线下转账",  # cjk-ok
        "alipay_transfer": "支付宝转账",  # cjk-ok
        "wechat_transfer": "微信转账",  # cjk-ok
    },
    "en-US": {
        "offline": "Offline transfer",
        "alipay_transfer": "Alipay transfer",
        "wechat_transfer": "WeChat transfer",
    },
}

_INVOICE_STATUS_LABEL: dict[str, dict[str, str]] = {
    "zh-CN": {"submitted": "审核中", "issued": "已开票", "rejected": "已驳回"},  # cjk-ok
    "en-US": {"submitted": "In review", "issued": "Issued", "rejected": "Rejected"},
}


def stream_hourly_csv(
    session: AsyncSession,
    user_id: int,
    *,
    month_range: tuple[datetime, datetime] | None = None,
    tz_offset_minutes: int,
    lang: str = "zh-CN",
) -> AsyncIterator[str]:
    """Hourly bill CSV (descending, newest first); month_range is the [start, end) UTC window."""
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
        header_row(_HEADERS[("hourly", lang)]),
        truncated_note=TRUNCATED_NOTES[lang],
    )


def stream_ledger_csv(
    session: AsyncSession,
    user_id: int,
    *,
    tz_offset_minutes: int,
    lang: str = "zh-CN",
) -> AsyncIterator[str]:
    """Ledger CSV (descending, newest first). Shared by the user side and the admin tenant
    drill-down."""
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
        header_row(_HEADERS[("ledger", lang)]),
        truncated_note=TRUNCATED_NOTES[lang],
    )


def stream_admin_orders_csv(
    session: AsyncSession,
    *,
    status: str | None = None,
    order_no: str | None = None,
    user_id: int | None = None,
    day_range: tuple[datetime, datetime] | None = None,
    tz_offset_minutes: int,
    lang: str = "zh-CN",
) -> AsyncIterator[str]:
    """Export top-up orders as CSV filtered by status, order number, user and creation-day window,
    by id descending."""
    stmt = wallet.admin_orders_query(
        status=status, order_no=order_no, user_id=user_id, day_range=day_range
    )
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
        header_row(_HEADERS[("admin_orders", lang)]),
        truncated_note=TRUNCATED_NOTES[lang],
    )


def stream_admin_refunds_csv(
    session: AsyncSession,
    *,
    status: str | None = None,
    day_range: tuple[datetime, datetime] | None = None,
    tz_offset_minutes: int,
    lang: str = "zh-CN",
) -> AsyncIterator[str]:
    """Export refund requests as CSV filtered by status and request-day window, by id descending."""
    stmt = refunds.admin_refunds_query(status=status, day_range=day_range)
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
        header_row(_HEADERS[("admin_refunds", lang)]),
        truncated_note=TRUNCATED_NOTES[lang],
    )


def mask_invoice_identity(value: str) -> str:
    """Mask an invoice title or email; keep the first character of multi-character strings, mask
    single characters fully, leave empty strings."""
    return account_service.mask_id_name(value) if value else value


def stream_admin_invoices_csv(
    session: AsyncSession,
    *,
    status: str | None = None,
    period: str | None = None,
    tz_offset_minutes: int,
    lang: str = "zh-CN",
    reveal: bool = False,
    row_counter: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    """Export invoices as CSV filtered by status and period, by id descending; title and email
    masked by default, tax id unmasked.

    With reveal=True the caller checks permission and reason and audits; row_counter accumulates
    the formatted data rows.
    """
    stmt = invoices.admin_invoices_query(status=status, period=period)
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
        header_row(_HEADERS[("admin_invoices", lang)]),
        truncated_note=TRUNCATED_NOTES[lang],
    )
