"""管理端子路由共享的响应/参数辅助(自 router.py 拆分)。"""

from datetime import datetime

from fastapi import Query
from fastapi.responses import StreamingResponse

from app.core.errors import AppError, ErrorCode

ExportLang = Query(default="zh-CN")


def csv_response(stream, filename: str) -> StreamingResponse:
    """CSV 流式响应:Content-Disposition 附件;截断标记行由流内部在触顶时追加。"""
    return StreamingResponse(
        stream,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def parse_day(day: str) -> tuple[datetime, datetime]:
    """UTC 日窗口 [start, end)(与 reconciliation 的 day 参数同口径)。"""
    from datetime import UTC, timedelta

    try:
        start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="adminapi.badDayFormat") from exc
    return start, start + timedelta(days=1)
