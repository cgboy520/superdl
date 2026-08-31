"""CSV 导出原语(用户端 billing 与管理端 adminapi 共用;core 层,不含任何业务查询)。

- 转义规则:BOM 头、含 ",\\n\\r 的字段加引号、公式前导字符(= + @ 制表/回车,
  或 - 开头且非纯数字)置 ' 文本化;
- 金额列保持 numeric 字符串原样,不做任何浮点运算;
- 时间按调用方时区偏移折算成墙钟并带 (UTC+x) 后缀,与 packages/ui formatDateTime 同口径;
- 单响应行数硬上限:触顶在文件末尾写截断标记行(前端据标记给「已截断」提示)。
"""

import re
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi.responses import StreamingResponse
from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.money import money_str

EXPORT_MAX_ROWS = 50_000
TRUNCATED_MARKER = "#SUPERDL_EXPORT_TRUNCATED#"
# 单次导出批拉粒度
EXPORT_BATCH = 1_000

# 触顶截断提示(双语,billing 用户端与 adminapi 管理端导出共用)
TRUNCATED_NOTES: dict[str, str] = {
    "zh-CN": "已达单次导出上限({limit} 行),仅导出前 {limit} 行;请缩小范围分次导出",
    "en-US": (
        "Export cap reached: only the first {limit} rows included;"
        " narrow the scope and export in parts"
    ),
}


def csv_response(stream: AsyncIterator[str], filename: str) -> StreamingResponse:
    """CSV 流式响应:Content-Disposition 附件;截断标记行由流内部在触顶时追加。"""
    return StreamingResponse(
        stream,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


_FORMULA_LEAD = frozenset("=+@\t\r")
_PLAIN_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")


def _esc(value: object) -> str:
    s = "" if value is None else str(value)
    if (s[:1] and s[0] in _FORMULA_LEAD) or (s.startswith("-") and not _PLAIN_NUMBER.match(s)):
        s = f"'{s}"
    if any(c in s for c in '",\n\r'):
        s = '"' + s.replace('"', '""') + '"'
    return s


def csv_line(values: Sequence[object]) -> str:
    return ",".join(_esc(v) for v in values) + "\r\n"


def utc_suffix(offset_minutes: int) -> str:
    """480 → "(UTC+8)";-300 → "(UTC-5)";345 → "(UTC+5:45)"。"""
    sign = "+" if offset_minutes >= 0 else "-"
    h, m = divmod(abs(offset_minutes), 60)
    return f"(UTC{sign}{h}:{m:02d})" if m else f"(UTC{sign}{h})"


def fmt_ts(ts: datetime, offset_minutes: int) -> str:
    local = ts + timedelta(minutes=offset_minutes)
    return f"{local.strftime('%Y-%m-%d %H:%M')} {utc_suffix(offset_minutes)}"


def fmt_money(value: Decimal) -> str:
    return money_str(value)


async def stream_rows(
    session: AsyncSession,
    stmt: Select[Any],
    id_col: InstrumentedAttribute[int],
    row_fn: Callable[[Any], Sequence[object]],
    headers: Sequence[str],
    *,
    truncated_note: str,
) -> AsyncIterator[str]:
    """流式 CSV 骨架:BOM + 表头,按 id 降序(最新在前)分批拉 stmt 的 ORM 行,
    单响应最多 EXPORT_MAX_ROWS 行;每批多取一行探测是否仍有剩余,触顶且有剩余即在
    文件末尾写截断标记行(truncated_note 用 {limit} 占位上限)。stmt 只带过滤条件,
    排序/游标/limit 由本函数施加。
    """
    yield "\ufeff" + csv_line(headers)  # BOM:防 Excel 中文乱码
    sent = 0
    last_id: int | None = None
    while True:
        want = min(EXPORT_BATCH, EXPORT_MAX_ROWS - sent)
        batch_stmt = stmt.order_by(id_col.desc()).limit(want + 1)
        if last_id is not None:
            batch_stmt = batch_stmt.where(id_col < last_id)
        rows = list((await session.execute(batch_stmt)).scalars())
        more = len(rows) > want
        rows = rows[:want]
        for r in rows:
            yield csv_line(row_fn(r))
        sent += len(rows)
        if not more:
            return
        if sent >= EXPORT_MAX_ROWS:
            yield f"{TRUNCATED_MARKER} {truncated_note.format(limit=EXPORT_MAX_ROWS)}\r\n"
            return
        last_id = getattr(rows[-1], id_col.key)
