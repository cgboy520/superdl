"""aware-UTC 时间统一入口;拒绝 naive datetime。"""

from datetime import UTC, datetime, timedelta


def now_utc() -> datetime:
    return datetime.now(UTC)


def ensure_utc(dt: datetime) -> datetime:
    """防御:拒绝 naive datetime,统一转 UTC。"""
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime is forbidden: {dt!r}")
    return dt.astimezone(UTC)


def hour_floor(dt: datetime) -> datetime:
    """所在自然小时的起点(UTC)。计费小时桶的键。"""
    dt = ensure_utc(dt)
    return dt.replace(minute=0, second=0, microsecond=0)


def day_floor(dt: datetime) -> datetime:
    """所在自然日的起点(UTC)。数据盘日结的键。"""
    dt = ensure_utc(dt)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def prev_hour_range(dt: datetime) -> tuple[datetime, datetime]:
    """上一个完整自然小时 [start, end)。小时结算的扫描窗口。"""
    end = hour_floor(dt)
    return end - timedelta(hours=1), end
