"""管理端 CSV 导出:审计检索 / 日对账。

- 转义/上限/截断标记与用户端账单导出同一套(app.core.csvexport);
  时间按调用方时区偏移折算并带 (UTC+x) 后缀;
- 审计导出的筛选口径与 GET /admin/v1/audit 完全一致(actor_type/actor_id/q/since/until);
- 对账导出 = GET /admin/v1/reconciliation 同一报告:首行合计,随后为 diff 超阈实例明细。
"""

import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.csvexport import EXPORT_MAX_ROWS, TRUNCATED_MARKER, csv_line, fmt_ts
from app.core.sqlutil import like_escape

_BATCH = 1_000

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
}

_TRUNCATED_NOTE: dict[str, str] = {
    "zh-CN": "已达单次导出上限({limit} 行),仅导出前 {limit} 行;请缩小检索范围分次导出",
    "en-US": (
        "Export cap reached: only the first {limit} rows included;"
        " narrow the filters and export in parts"
    ),
}

_TOTAL_LABEL = {"zh-CN": "合计", "en-US": "TOTAL"}


def _audit_filters(stmt, *, actor_type, actor_id, q, since, until):
    """与 GET /admin/v1/audit 同一组 where 条件(含 LIKE 元字符转义)。"""
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


async def stream_audit_csv(
    session: AsyncSession,
    *,
    actor_type: str | None = None,
    actor_id: str | None = None,
    q: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    tz_offset_minutes: int = 480,
    lang: str = "zh-CN",
) -> AsyncIterator[str]:
    """审计日志 CSV(降序,最新在前;按 id 批拉直至上限或穷尽,触顶写截断标记行)。"""
    yield "\ufeff" + csv_line(_HEADERS[("audit", lang)])  # BOM:防 Excel 中文乱码
    sent = 0
    last_id: int | None = None
    exhausted = False
    while sent < EXPORT_MAX_ROWS:
        want = min(_BATCH, EXPORT_MAX_ROWS - sent)
        stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(want)
        stmt = _audit_filters(
            stmt, actor_type=actor_type, actor_id=actor_id, q=q, since=since, until=until
        )
        if last_id is not None:
            stmt = stmt.where(AuditLog.id < last_id)
        rows = list((await session.execute(stmt)).scalars())
        for r in rows:
            yield csv_line(
                [
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
            )
        sent += len(rows)
        if rows:
            last_id = rows[-1].id
        if len(rows) < want:
            exhausted = True
            break
    if not exhausted and last_id is not None:
        probe = select(AuditLog.id).where(AuditLog.id < last_id).limit(1)
        probe = _audit_filters(
            probe, actor_type=actor_type, actor_id=actor_id, q=q, since=since, until=until
        )
        if (await session.execute(probe)).first() is not None:
            note = _TRUNCATED_NOTE[lang].format(limit=EXPORT_MAX_ROWS)
            yield f"{TRUNCATED_MARKER} {note}\r\n"


async def stream_reconciliation_csv(
    report: dict[str, Any],
    *,
    lang: str = "zh-CN",
) -> AsyncIterator[str]:
    """日对账 CSV:首行合计(实例列标「合计」),随后 diff 超阈实例明细;明细超上限同样截断。"""
    yield "\ufeff" + csv_line(_HEADERS[("reconciliation", lang)])
    yield csv_line(
        [
            _TOTAL_LABEL[lang],
            report["billed_total"],
            report["estimated_total"],
            report["diff_pct"],
        ]
    )
    for sent, o in enumerate(report["outliers"]):
        if sent >= EXPORT_MAX_ROWS:
            note = _TRUNCATED_NOTE[lang].format(limit=EXPORT_MAX_ROWS)
            yield f"{TRUNCATED_MARKER} {note}\r\n"
            break
        yield csv_line([o["instance_id"], o["billed"], o["estimated"], o["diff_pct"]])
