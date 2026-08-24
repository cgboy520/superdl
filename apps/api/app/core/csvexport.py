"""CSV 导出原语(用户端 billing 与管理端 adminapi 共用;core 层,不含任何业务查询)。

- 转义规则:BOM 头、含 ",\\n\\r 的字段加引号、公式前导字符(= + @ 制表/回车,
  或 - 开头且非纯数字)置 ' 文本化;
- 金额列保持 numeric 字符串原样,不做任何浮点运算;
- 时间按调用方时区偏移折算成墙钟并带 (UTC+x) 后缀,与 packages/ui formatDateTime 同口径;
- 单响应行数硬上限:触顶在文件末尾写截断标记行(前端据标记给「已截断」提示)。
"""

import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

EXPORT_MAX_ROWS = 50_000
TRUNCATED_MARKER = "#SUPERDL_EXPORT_TRUNCATED#"

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
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    local = ts + timedelta(minutes=offset_minutes)
    return f"{local.strftime('%Y-%m-%d %H:%M')} {utc_suffix(offset_minutes)}"


def fmt_money(value: Decimal) -> str:
    return format(value, "f")
