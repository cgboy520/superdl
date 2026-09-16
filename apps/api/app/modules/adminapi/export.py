"""Audit, daily reconciliation and adjustment CSV exports; timestamps shifted by the caller's time
zone offset."""

import json
from collections.abc import AsyncIterator
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.csvexport import (
    TRUNCATED_NOTES,
    csv_line,
    fmt_money,
    fmt_ts,
    header_row,
    stream_rows,
)
from app.core.sqlutil import like_escape
from app.modules.adminapi.models import AdminAdjustment
from app.modules.metering.schemas import ReconciliationOut

_HEADERS: dict[tuple[str, str], list[str]] = {
    ("audit", "zh-CN"): [
        "ID",
        "操作者类型",  # cjk-ok
        "操作者",  # cjk-ok
        "动作",  # cjk-ok
        "目标",  # cjk-ok
        "IP",
        "结果",  # cjk-ok
        "详情",  # cjk-ok
        "时间",  # cjk-ok
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
    ("reconciliation", "zh-CN"): [
        "实例ID",  # cjk-ok
        "事件计费({currency})",  # cjk-ok
        "指标估算({currency})",  # cjk-ok
        "diff%",
    ],
    ("reconciliation", "en-US"): [
        "Instance ID",
        "Billed ({currency})",
        "Estimated ({currency})",
        "diff%",
    ],
    ("adjustments", "zh-CN"): [
        "ID",
        "用户ID",  # cjk-ok
        "金额({currency})",  # cjk-ok
        "状态",  # cjk-ok
        "事由",  # cjk-ok
        "发起人",  # cjk-ok
        "复核人",  # cjk-ok
        "创建时间",  # cjk-ok
    ],
    ("adjustments", "en-US"): [
        "ID",
        "User ID",
        "Amount ({currency})",
        "Status",
        "Reason",
        "Created by",
        "Reviewed by",
        "Created at",
    ],
}

_ADJUSTMENT_STATUS_LABEL: dict[str, dict[str, str]] = {
    "zh-CN": {"pending": "待复核", "approved": "已生效", "rejected": "已驳回"},  # cjk-ok
    "en-US": {"pending": "Pending review", "approved": "Effective", "rejected": "Rejected"},
}

_TOTAL_LABEL = {"zh-CN": "合计", "en-US": "TOTAL"}  # cjk-ok


def audit_filters(stmt, *, actor_type, actor_id, q, since, until):
    """Filter by actor, action or target substring and the [since, until) window; %/_ in q match
    literally."""
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
    tz_offset_minutes: int,
    lang: str = "zh-CN",
) -> AsyncIterator[str]:
    """Audit log CSV (descending, newest first; pulled by id in batches until the cap or the end, a
    truncation marker row when the cap is hit)."""
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
        header_row(_HEADERS[("audit", lang)]),
        truncated_note=TRUNCATED_NOTES[lang],
    )


async def stream_reconciliation_csv(
    report: ReconciliationOut,
    *,
    lang: str = "zh-CN",
) -> AsyncIterator[str]:
    """Daily reconciliation CSV: the total first, then the instances above the diff threshold."""
    yield "\ufeff" + csv_line(header_row(_HEADERS[("reconciliation", lang)]))
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
    tz_offset_minutes: int,
    lang: str = "zh-CN",
) -> AsyncIterator[str]:
    """Export adjustments as CSV filtered by status, user and creation-day window, by id
    descending."""
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
        header_row(_HEADERS[("adjustments", lang)]),
        truncated_note=TRUNCATED_NOTES[lang],
    )
