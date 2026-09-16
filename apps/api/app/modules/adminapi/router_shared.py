"""Response / parameter helpers shared by the admin sub-routers."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import Depends, Query

from app.core.compliance import current_profile
from app.core.timeutil import billing_day_range


def _export_lang(lang: Literal["zh-CN", "en-US"] | None = Query(default=None)) -> str:
    """CSV language; defaults to the compliance profile's default locale."""
    return lang or current_profile().default_locale


ExportLang = Depends(_export_lang)


def parse_day(day: str) -> tuple[datetime, datetime]:
    """YYYY-MM-DD in the billing zone → UTC [start, end); bad format → VALIDATION_ERROR."""
    return billing_day_range(day, key="adminapi.badDayFormat")


def _day_range(day: str | None = Query(default=None)) -> tuple[datetime, datetime] | None:
    """Optional day=YYYY-MM-DD (billing zone) → UTC [start, end); absent = no filter."""
    return parse_day(day) if day else None


DayRange = Annotated[tuple[datetime, datetime] | None, Depends(_day_range)]


def day_suffix(day_range: tuple[datetime, datetime] | None) -> str:
    """CSV file name suffix: YYYY-MM-DD or all."""
    return f"{day_range[0]:%Y-%m-%d}" if day_range else "all"
