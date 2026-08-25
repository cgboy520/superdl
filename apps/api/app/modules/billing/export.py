"""账单 CSV 导出:流式生成,单响应行数硬上限 + 截断标记行。

转义/时区/上限原语在 app.core.csvexport(管理端导出共用);本文件只留业务表查询与列定义。
"""

from collections.abc import AsyncIterator
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.csvexport import EXPORT_MAX_ROWS, TRUNCATED_MARKER, csv_line, fmt_money, fmt_ts

# 单次导出批拉粒度
_BATCH = 1_000

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

_TRUNCATED_NOTE: dict[str, str] = {
    "zh-CN": "已达单次导出上限({limit} 行),仅导出前 {limit} 行;请缩小时间范围分次导出",
    "en-US": (
        "Export cap reached: only the first {limit} rows included;"
        " narrow the time range and export in parts"
    ),
}


async def stream_hourly_csv(
    session: AsyncSession,
    user_id: int,
    *,
    month_range: tuple[datetime, datetime] | None = None,
    tz_offset_minutes: int = 480,
    lang: str = "zh-CN",
) -> AsyncIterator[str]:
    """小时账单 CSV(降序,最新在前);month_range 为 [起, 讫) UTC 窗口。"""
    from app.modules.billing.models import BillHourly

    yield "\ufeff" + csv_line(_HEADERS[("hourly", lang)])  # BOM:防 Excel 中文乱码
    sent = 0
    last_id: int | None = None
    exhausted = False
    while sent < EXPORT_MAX_ROWS:
        want = min(_BATCH, EXPORT_MAX_ROWS - sent)
        stmt = (
            select(BillHourly)
            .where(BillHourly.user_id == user_id)
            .order_by(BillHourly.id.desc())
            .limit(want)
        )
        if month_range is not None:
            stmt = stmt.where(
                BillHourly.hour_start >= month_range[0], BillHourly.hour_start < month_range[1]
            )
        if last_id is not None:
            stmt = stmt.where(BillHourly.id < last_id)
        rows = list((await session.execute(stmt)).scalars())
        for r in rows:
            yield csv_line(
                [
                    fmt_ts(r.hour_start, tz_offset_minutes),
                    r.instance_id,
                    r.seconds_used,
                    fmt_money(r.unit_price),
                    r.gpu_count,
                    fmt_money(r.amount),
                ]
            )
        sent += len(rows)
        if rows:
            last_id = rows[-1].id
        if len(rows) < want:
            exhausted = True
            break
    if not exhausted and last_id is not None:
        # 触顶再探一行:仍有剩余则在文件末尾写截断标记行
        probe = (
            select(BillHourly.id)
            .where(BillHourly.user_id == user_id, BillHourly.id < last_id)
            .limit(1)
        )
        if month_range is not None:
            probe = probe.where(
                BillHourly.hour_start >= month_range[0], BillHourly.hour_start < month_range[1]
            )
        if (await session.execute(probe)).first() is not None:
            note = _TRUNCATED_NOTE[lang].format(limit=EXPORT_MAX_ROWS)
            yield f"{TRUNCATED_MARKER} {note}\r\n"


async def stream_ledger_csv(
    session: AsyncSession,
    user_id: int,
    *,
    tz_offset_minutes: int = 480,
    lang: str = "zh-CN",
) -> AsyncIterator[str]:
    """资金流水 CSV(降序,最新在前)。用户端与管理端租户下钻导出共用。"""
    from app.modules.billing.models import BalanceLedger

    yield "\ufeff" + csv_line(_HEADERS[("ledger", lang)])  # BOM:防 Excel 中文乱码
    labels = _LEDGER_TYPE_LABEL[lang]
    sent = 0
    last_id: int | None = None
    exhausted = False
    while sent < EXPORT_MAX_ROWS:
        want = min(_BATCH, EXPORT_MAX_ROWS - sent)
        stmt = (
            select(BalanceLedger)
            .where(BalanceLedger.user_id == user_id)
            .order_by(BalanceLedger.id.desc())
            .limit(want)
        )
        if last_id is not None:
            stmt = stmt.where(BalanceLedger.id < last_id)
        rows = list((await session.execute(stmt)).scalars())
        for r in rows:
            yield csv_line(
                [
                    fmt_ts(r.created_at, tz_offset_minutes),
                    labels.get(r.type, r.type),
                    fmt_money(r.amount),
                    fmt_money(r.balance_after),
                    f"{r.ref_type}:{r.ref_id or ''}" if r.ref_type else "",
                    r.remark or "",
                ]
            )
        sent += len(rows)
        if rows:
            last_id = rows[-1].id
        if len(rows) < want:
            exhausted = True
            break
    if not exhausted and last_id is not None:
        probe = (
            select(BalanceLedger.id)
            .where(BalanceLedger.user_id == user_id, BalanceLedger.id < last_id)
            .limit(1)
        )
        if (await session.execute(probe)).first() is not None:
            note = _TRUNCATED_NOTE[lang].format(limit=EXPORT_MAX_ROWS)
            yield f"{TRUNCATED_MARKER} {note}\r\n"


async def stream_admin_orders_csv(
    session: AsyncSession,
    *,
    status: str | None = None,
    order_no: str | None = None,
    user_id: int | None = None,
    day_range: tuple[datetime, datetime] | None = None,
    tz_offset_minutes: int = 480,
    lang: str = "zh-CN",
) -> AsyncIterator[str]:
    """管理端充值订单 CSV(降序;筛选口径与 GET /admin/v1/orders 一致)。"""
    from app.modules.billing.models import Order

    yield "\ufeff" + csv_line(_HEADERS[("admin_orders", lang)])  # BOM:防 Excel 中文乱码
    status_labels = _ORDER_STATUS_LABEL[lang]
    channel_labels = _ORDER_CHANNEL_LABEL[lang]
    sent = 0
    last_id: int | None = None
    exhausted = False
    while sent < EXPORT_MAX_ROWS:
        want = min(_BATCH, EXPORT_MAX_ROWS - sent)
        stmt = select(Order).order_by(Order.id.desc()).limit(want)
        if status:
            stmt = stmt.where(Order.status == status)
        if order_no:
            stmt = stmt.where(Order.order_no == order_no.strip())
        if user_id:
            stmt = stmt.where(Order.user_id == user_id)
        if day_range is not None:
            stmt = stmt.where(Order.created_at >= day_range[0], Order.created_at < day_range[1])
        if last_id is not None:
            stmt = stmt.where(Order.id < last_id)
        rows = list((await session.execute(stmt)).scalars())
        for r in rows:
            yield csv_line(
                [
                    r.order_no,
                    r.user_id,
                    fmt_money(r.amount),
                    channel_labels.get(r.channel, r.channel),
                    status_labels.get(r.status, r.status),
                    fmt_ts(r.paid_at, tz_offset_minutes) if r.paid_at else "",
                    fmt_ts(r.created_at, tz_offset_minutes),
                ]
            )
        sent += len(rows)
        if rows:
            last_id = rows[-1].id
        if len(rows) < want:
            exhausted = True
            break
    if not exhausted and last_id is not None:
        probe = select(Order.id).where(Order.id < last_id).limit(1)
        if status:
            probe = probe.where(Order.status == status)
        if order_no:
            probe = probe.where(Order.order_no == order_no.strip())
        if user_id:
            probe = probe.where(Order.user_id == user_id)
        if day_range is not None:
            probe = probe.where(Order.created_at >= day_range[0], Order.created_at < day_range[1])
        if (await session.execute(probe)).first() is not None:
            note = _TRUNCATED_NOTE[lang].format(limit=EXPORT_MAX_ROWS)
            yield f"{TRUNCATED_MARKER} {note}\r\n"
