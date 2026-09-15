"""审计、日对账与调账单 CSV 导出;时间按调用方时区偏移折算。"""

import json
from collections.abc import AsyncIterator
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.csvexport import TRUNCATED_NOTES, csv_line, fmt_money, fmt_ts, stream_rows
from app.core.sqlutil import like_escape
from app.core.timeutil import BILLING_TZ_OFFSET_MINUTES
from app.modules.adminapi.models import AdminAdjustment
from app.modules.metering.schemas import ReconciliationOut

_HEADERS: dict[tuple[str, str], list[str]] = {
    ("audit", "zh-CN"): [
        "ID",
        "操作者类型",
        "操作者",
        "动作",
        "目标",
        "IP",
        "结果",
        "详情",
        "时间",
    ],
    ("audit", "en-US"): [
        "ID",
        "Actor type",
        "Actor",
        "Action",
        "Target",
        "IP",
        "Result",
        "Detail",
        "Time",
    ],
    ("reconciliation", "zh-CN"): ["实例ID", "事件计费(元)", "指标估算(元)", "diff%"],
    ("reconciliation", "en-US"): ["Instance ID", "Billed (CNY)", "Estimated (CNY)", "diff%"],
    ("adjustments", "zh-CN"): [
        "ID",
        "用户ID",
        "金额(元)",
        "状态",
        "事由",
        "发起人",
        "复核人",
        "创建时间",
    ],
    ("adjustments", "en-US"): [
        "ID",
        "User ID",
        "Amount (CNY)",
        "Status",
        "Reason",
        "Created by",
        "Reviewed by",
        "Created at",
    ],
}

_ADJUSTMENT_STATUS_LABEL: dict[str, dict[str, str]] = {
    "zh-CN": {"pending": "待复核", "approved": "已生效", "rejected": "已驳回"},
    "en-US": {"pending": "Pending review", "approved": "Effective", "rejected": "Rejected"},
}

_TOTAL_LABEL = {"zh-CN": "合计", "en-US": "TOTAL"}


def audit_filters(stmt, *, actor_type, actor_id, q, since, until):
    """按操作者、动作或目标子串及 [since, until) 时间窗过滤;q 的 %/_ 按字面匹配。"""
    if actor_type:
        stmt = stmt.where(AuditLog.actor_type == actor_type)
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if q:
        pattern = f"%{like_escape(q)}%"
        stmt = stmt.where(
            AuditLog.action.ilike(pattern, escape="\\")
            | AuditLog.target.ilike(pattern, escape="\\")
        )
    if since:
        stmt = stmt.where(AuditLog.created_at >= since)
    if until:
        stmt = stmt.where(AuditLog.created_at < until)
    return stmt


def stream_audit_csv(
    session: AsyncSession,
    *,
    actor_type: str | None = None,
    actor_id: str | None = None,
    q: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    tz_offset_minutes: int = BILLING_TZ_OFFSET_MINUTES,
    lang: str = "zh-CN",
) -> AsyncIterator[str]:
    """审计日志 CSV(降序,最新在前;按 id 批拉直至上限或穷尽,触顶写截断标记行)。"""
    stmt = audit_filters(
        select(AuditLog), actor_type=actor_type, actor_id=actor_id, q=q, since=since, until=until
    )

    def row(r: AuditLog) -> list[object]:
        return [
            r.id,
            r.actor_type,
            r.actor_id or "",
            r.action,
            r.target or "",
            str(r.ip) if r.ip else "",
            r.result,
            json.dumps(r.detail, ensure_ascii=False) if r.detail else "",
            fmt_ts(r.created_at, tz_offset_minutes),
        ]

    return stream_rows(
        session,
        stmt,
        AuditLog.id,
        row,
        _HEADERS[("audit", lang)],
        truncated_note=TRUNCATED_NOTES[lang],
    )


async def stream_reconciliation_csv(
    report: ReconciliationOut,
    *,
    lang: str = "zh-CN",
) -> AsyncIterator[str]:
    """日对账 CSV:首行合计,随后 diff 超阈实例明细。"""
    yield "\ufeff" + csv_line(_HEADERS[("reconciliation", lang)])
    yield csv_line(
        [
            _TOTAL_LABEL[lang],
            report.billed_total,
            report.estimated_total,
            report.diff_pct,
        ]
    )
    for o in report.outliers:
        yield csv_line([o.instance_id, o.billed, o.estimated, o.diff_pct])


def stream_adjustments_csv(
    session: AsyncSession,
    *,
    status: str | None = None,
    user_id: int | None = None,
    day_range: tuple[datetime, datetime] | None = None,
    tz_offset_minutes: int = BILLING_TZ_OFFSET_MINUTES,
    lang: str = "zh-CN",
) -> AsyncIterator[str]:
    """按状态、用户与创建日窗口导出调账单 CSV,按 id 降序。"""
    stmt = select(AdminAdjustment)
    if status:
        stmt = stmt.where(AdminAdjustment.status == status)
    if user_id is not None:
        stmt = stmt.where(AdminAdjustment.user_id == user_id)
    if day_range is not None:
        stmt = stmt.where(
            AdminAdjustment.created_at >= day_range[0],
            AdminAdjustment.created_at < day_range[1],
        )
    status_labels = _ADJUSTMENT_STATUS_LABEL[lang]

    def row(r: AdminAdjustment) -> list[object]:
        return [
            r.id,
            r.user_id,
            fmt_money(r.amount),
            status_labels.get(r.status, r.status),
            r.reason,
            r.created_by,
            r.reviewed_by if r.reviewed_by is not None else "",
            fmt_ts(r.created_at, tz_offset_minutes),
        ]

    return stream_rows(
        session,
        stmt,
        AdminAdjustment.id,
        row,
        _HEADERS[("adjustments", lang)],
        truncated_note=TRUNCATED_NOTES[lang],
    )
