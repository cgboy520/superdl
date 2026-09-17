"""管理端子路由共享的响应/参数辅助。"""

from datetime import datetime
from typing import Annotated

from fastapi import Depends, Query

from app.core.timeutil import billing_day_range

ExportLang = Query(default="zh-CN")


def parse_day(day: str) -> tuple[datetime, datetime]:
    """YYYY-MM-DD in the billing zone → UTC [start, end); bad format → VALIDATION_ERROR."""
    return billing_day_range(day, key="adminapi.badDayFormat")


def _day_range(day: str | None = Query(default=None)) -> tuple[datetime, datetime] | None:
    """Optional day=YYYY-MM-DD (billing zone) → UTC [start, end); absent = no filter."""
    return parse_day(day) if day else None


DayRange = Annotated[tuple[datetime, datetime] | None, Depends(_day_range)]


def day_suffix(day_range: tuple[datetime, datetime] | None) -> str:
    """CSV 文件名尾缀:YYYY-MM-DD 或 all。"""
    return f"{day_range[0]:%Y-%m-%d}" if day_range else "all"
