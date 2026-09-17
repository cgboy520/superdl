"""Aware-UTC time helpers (naive datetimes are rejected) and the deployment's billing calendar:
billing days and months are civil days/months in `SUPERDL_BILLING_TIMEZONE` (IANA, DST-aware),
expressed as UTC instants. Client-side aggregation helpers take an explicit offset."""

import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode


def now_utc() -> datetime:
    return datetime.now(UTC)


def ensure_utc(dt: datetime) -> datetime:
    """Convert to UTC; naive datetimes are rejected outright."""
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime is forbidden: {dt!r}")
    return dt.astimezone(UTC)


def hour_floor(dt: datetime) -> datetime:
    """Start of the containing clock hour (UTC). The key of the billing hour bucket."""
    dt = ensure_utc(dt)
    return dt.replace(minute=0, second=0, microsecond=0)


def billing_zone() -> ZoneInfo:
    """The deployment's billing time zone (`SUPERDL_BILLING_TIMEZONE`)."""
    return ZoneInfo(get_settings().billing_timezone)


def billing_offset_minutes(at: datetime | None = None) -> int:
    """UTC offset of the billing zone at `at` (default now), in minutes; DST-aware."""
    ref = ensure_utc(at) if at is not None else now_utc()
    offset = ref.astimezone(billing_zone()).utcoffset() or timedelta(0)
    return int(offset.total_seconds() // 60)


def billing_local_date(dt: datetime) -> date:
    """Civil date of a UTC instant in the billing zone."""
    return ensure_utc(dt).astimezone(billing_zone()).date()


def _local_midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=billing_zone()).astimezone(UTC)


def billing_day_floor(dt: datetime) -> datetime:
    """Start of the billing day containing `dt`, as a UTC instant. Key of daily disk bills and
    the fund reconciliation."""
    return _local_midnight(billing_local_date(dt))


def billing_day_shift(day_start: datetime, days: int) -> datetime:
    """`days` billing days after (negative: before) a day start; DST days are 23 or 25 hours, so
    never add `timedelta(days=1)` to a day start."""
    return _local_midnight(billing_local_date(day_start) + timedelta(days=days))


def billing_day_range(day: str, *, key: str = "billing.badDateFormat") -> tuple[datetime, datetime]:
    """YYYY-MM-DD in the billing zone → UTC [start, end); bad format → VALIDATION_ERROR(key)."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise AppError(ErrorCode.VALIDATION_ERROR, key=key)
    try:
        local_day = date.fromisoformat(day)
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, key=key) from exc
    start = _local_midnight(local_day)
    return start, billing_day_shift(start, 1)


def billing_period(dt: datetime) -> str:
    """Billing month (YYYY-MM) of a UTC instant in the billing zone; invoice period key."""
    return f"{ensure_utc(dt).astimezone(billing_zone()):%Y-%m}"


def current_billing_period() -> str:
    return billing_period(now_utc())


def billing_period_range(period: str) -> tuple[datetime, datetime]:
    """YYYY-MM in the billing zone → UTC [start, end); bad format → VALIDATION_ERROR."""
    match = re.fullmatch(r"(\d{4})-(\d{2})", period)
    try:
        if match is None:
            raise ValueError(period)
        first = date(int(match.group(1)), int(match.group(2)), 1)
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="billing.badMonthFormat") from exc
    next_first = (
        first.replace(year=first.year + 1, month=1)
        if first.month == 12
        else first.replace(month=first.month + 1)
    )
    return _local_midnight(first), _local_midnight(next_first)


def billing_month_range(month: str, *, tz_offset_minutes: int) -> tuple[datetime, datetime]:
    """YYYY-MM at a fixed client offset → UTC [start, end) (display aggregation by the caller's
    clock; the billing calendar itself uses `billing_period_range`)."""
    try:
        local_start = datetime.strptime(month, "%Y-%m").replace(tzinfo=UTC)
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="billing.badMonthFormat") from exc
    local_end = (
        local_start.replace(year=local_start.year + 1, month=1)
        if local_start.month == 12
        else local_start.replace(month=local_start.month + 1)
    )
    offset = timedelta(minutes=tz_offset_minutes)
    return local_start - offset, local_end - offset


def prev_hour_range(dt: datetime) -> tuple[datetime, datetime]:
    """The previous complete clock hour [start, end). The scan window of hourly settlement."""
    end = hour_floor(dt)
    return end - timedelta(hours=1), end


def local_day_range(
    tz_offset_minutes: int, *, at: datetime | None = None
) -> tuple[datetime, datetime]:
    """Local day [start, end) as UTC instants for a fixed client offset (480 = UTC+8); `at`
    defaults to now."""
    offset = timedelta(minutes=tz_offset_minutes)
    ref = ensure_utc(at) if at is not None else now_utc()
    day_start = (ref + offset).replace(hour=0, minute=0, second=0, microsecond=0) - offset
    return day_start, day_start + timedelta(days=1)


def parse_local_date(date: str, tz_offset_minutes: int) -> tuple[datetime, datetime]:
    """YYYY-MM-DD at a fixed client offset → UTC [start, end); bad format → VALIDATION_ERROR."""
    try:
        local_midnight = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="billing.badDateFormat") from exc
    start = local_midnight - timedelta(minutes=tz_offset_minutes)
    return start, start + timedelta(days=1)
