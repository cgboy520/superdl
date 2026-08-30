"""管理端子路由共享的响应/参数辅助。"""

from datetime import datetime

from fastapi import Query

from app.core.errors import AppError, ErrorCode

ExportLang = Query(default="zh-CN")


def parse_day(day: str) -> tuple[datetime, datetime]:
    """UTC 日窗口 [start, end)(与 reconciliation 的 day 参数同口径)。"""
    from datetime import UTC, timedelta

    try:
        start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="adminapi.badDayFormat") from exc
    return start, start + timedelta(days=1)
