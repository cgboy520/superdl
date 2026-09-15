"""管理端子路由共享的响应/参数辅助。"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Query

from app.core.errors import AppError, ErrorCode

ExportLang = Query(default="zh-CN")


def parse_day(day: str) -> tuple[datetime, datetime]:
    """将 YYYY-MM-DD 解析为 UTC 日窗口 [start, end);格式错误抛 VALIDATION_ERROR。"""
    try:
        start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="adminapi.badDayFormat") from exc
    return start, start + timedelta(days=1)


def _day_range(day: str | None = Query(default=None)) -> tuple[datetime, datetime] | None:
    """可选 day=YYYY-MM-DD → UTC 日窗口 [start, end);缺省不过滤。"""
    return parse_day(day) if day else None


DayRange = Annotated[tuple[datetime, datetime] | None, Depends(_day_range)]


def day_suffix(day_range: tuple[datetime, datetime] | None) -> str:
    """CSV 文件名尾缀:YYYY-MM-DD 或 all。"""
    return f"{day_range[0]:%Y-%m-%d}" if day_range else "all"
