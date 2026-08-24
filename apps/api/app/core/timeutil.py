"""aware-UTC 时间统一入口;拒绝 naive datetime。"""

from datetime import UTC, datetime, timedelta


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


def day_floor(dt: datetime) -> datetime:
    """所在自然日的起点(UTC)。计费口径的「日」一律用 billing_day_floor(北京日界)。"""
    dt = ensure_utc(dt)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


# 计费日界按北京时间(UTC+8):面向中国用户,「3 月 1 日」的盘费应覆盖北京 3/1 全天,
# 而非 UTC 日(北京 3/1 08:00 – 3/2 08:00)
BILLING_DAY_OFFSET = timedelta(hours=8)


def billing_day_floor(dt: datetime) -> datetime:
    """所在北京自然日的起点(以 UTC 时刻表示)。数据盘日结与资金日对账的键。"""
    dt = ensure_utc(dt)
    shifted = (dt + BILLING_DAY_OFFSET).replace(hour=0, minute=0, second=0, microsecond=0)
    return shifted - BILLING_DAY_OFFSET


def prev_hour_range(dt: datetime) -> tuple[datetime, datetime]:
    """上一个完整自然小时 [start, end)。小时结算的扫描窗口。"""
    end = hour_floor(dt)
    return end - timedelta(hours=1), end
