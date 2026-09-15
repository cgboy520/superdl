"""计费 CSV 流式导出;行数受上限约束,超限写截断标记行。"""

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.csvexport import TRUNCATED_NOTES, fmt_money, fmt_ts, stream_rows
from app.core.timeutil import BILLING_TZ_OFFSET_MINUTES
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
    """按状态、订单号、用户与创建日窗口导出充值订单 CSV,按 id 降序。"""
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
    """按状态与申请日窗口导出退款单 CSV,按 id 降序。"""
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
        _HEADERS[("admin_refunds", lang)],
        truncated_note=TRUNCATED_NOTES[lang],
    )


def mask_invoice_identity(value: str) -> str:
    """脱敏发票抬头或邮箱;保留多字串首字,单字全掩,空串不变。"""
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
    """按状态与账期导出发票 CSV,按 id 降序;默认脱敏抬头与邮箱,税号不脱敏。

    reveal=True 时调用方须校验权限与事由并落审计;row_counter 累计已格式化的数据行数。
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
        _HEADERS[("admin_invoices", lang)],
        truncated_note=TRUNCATED_NOTES[lang],
    )
