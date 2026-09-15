from datetime import UTC, datetime

import pytest

from app.core.errors import AppError, ErrorCode
from app.core.timeutil import (
    billing_month_range,
    ensure_utc,
    local_day_range,
    parse_local_date,
)


def test_ensure_utc_rejects_naive():
    with pytest.raises(ValueError):
        ensure_utc(datetime(2026, 1, 1))  # noqa: DTZ001


def test_billing_month_range():
    """本地自然月折 UTC 窗口:默认北京账期;12 月进位到次年;格式非法报 VALIDATION_ERROR。"""
    assert billing_month_range("2026-08") == (
        datetime(2026, 7, 31, 16, 0, tzinfo=UTC),
        datetime(2026, 8, 31, 16, 0, tzinfo=UTC),
    )
    assert billing_month_range("2026-12", tz_offset_minutes=-300) == (
        datetime(2026, 12, 1, 5, 0, tzinfo=UTC),
        datetime(2027, 1, 1, 5, 0, tzinfo=UTC),
    )
    for bad in ("2026-13", "2026/08", "bad"):
        with pytest.raises(AppError) as exc:
            billing_month_range(bad)
        assert exc.value.code is ErrorCode.VALIDATION_ERROR


def test_local_day_range():
    """参数化本地日界:东八区 8/19 全天 = UTC 8/18 16:00 ~ 8/19 16:00。"""
    at = datetime(2026, 8, 19, 3, 30, tzinfo=UTC)
    start, end = local_day_range(480, at=at)
    assert start == datetime(2026, 8, 18, 16, 0, tzinfo=UTC)
    assert end == datetime(2026, 8, 19, 16, 0, tzinfo=UTC)


def test_parse_local_date():
    """YYYY-MM-DD 本地日折 UTC 窗口;格式非法报 VALIDATION_ERROR。"""
    start, end = parse_local_date("2026-08-19", 480)
    assert start == datetime(2026, 8, 18, 16, 0, tzinfo=UTC)
    assert end == datetime(2026, 8, 19, 16, 0, tzinfo=UTC)
    with pytest.raises(AppError) as exc:
        parse_local_date("2026/08/19", 480)
    assert exc.value.code is ErrorCode.VALIDATION_ERROR
