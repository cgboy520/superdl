"""aware-UTC 时间统一入口;拒绝 naive datetime。"""

from datetime import UTC, datetime, timedelta

from app.core.errors import AppError, ErrorCode


def now_utc() -> datetime:
    return datetime.now(UTC)


def ensure_utc(dt: datetime) -> datetime:
    """转 UTC;naive datetime 直接拒绝。"""
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime is forbidden: {dt!r}")
    return dt.astimezone(UTC)


def hour_floor(dt: datetime) -> datetime:
    """所在自然小时的起点(UTC)。计费小时桶的键。"""
    dt = ensure_utc(dt)
    return dt.replace(minute=0, second=0, microsecond=0)


# 计费日界按北京时间(UTC+8)
BILLING_TZ_OFFSET_MINUTES = 480
BILLING_DAY_OFFSET = timedelta(minutes=BILLING_TZ_OFFSET_MINUTES)


def billing_day_floor(dt: datetime) -> datetime:
    """所在北京自然日的起点(以 UTC 时刻表示)。数据盘日结与资金日对账的键。"""
    dt = ensure_utc(dt)
    shifted = (dt + BILLING_DAY_OFFSET).replace(hour=0, minute=0, second=0, microsecond=0)
    return shifted - BILLING_DAY_OFFSET


def billing_month_range(
    month: str, *, tz_offset_minutes: int = BILLING_TZ_OFFSET_MINUTES
) -> tuple[datetime, datetime]:
    """YYYY-MM 本地自然月的 UTC [start, end) 窗口(默认北京时间);格式非法报 VALIDATION_ERROR。"""
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
    """上一个完整自然小时 [start, end)。小时结算的扫描窗口。"""
    end = hour_floor(dt)
    return end - timedelta(hours=1), end


def local_day_range(
    tz_offset_minutes: int, *, at: datetime | None = None
) -> tuple[datetime, datetime]:
    """本地自然日 [start, end)(UTC 时刻);tz_offset_minutes 东八区为 480,at 缺省 now_utc()。"""
    offset = timedelta(minutes=tz_offset_minutes)
    ref = ensure_utc(at) if at is not None else now_utc()
    day_start = (ref + offset).replace(hour=0, minute=0, second=0, microsecond=0) - offset
    return day_start, day_start + timedelta(days=1)


def parse_local_date(date: str, tz_offset_minutes: int) -> tuple[datetime, datetime]:
    """YYYY-MM-DD 本地自然日的 UTC [start, end) 窗口;格式非法报 VALIDATION_ERROR。"""
    try:
        local_midnight = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="billing.badDateFormat") from exc
    start = local_midnight - timedelta(minutes=tz_offset_minutes)
    return start, start + timedelta(days=1)
