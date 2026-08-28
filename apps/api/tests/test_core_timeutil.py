from datetime import UTC, datetime

import pytest

from app.core.errors import AppError, ErrorCode
from app.core.timeutil import (
    billing_month_range,
    ensure_utc,
    hour_floor,
    prev_hour_range,
)


def test_ensure_utc_rejects_naive():
    with pytest.raises(ValueError):
        ensure_utc(datetime(2026, 1, 1))  # noqa: DTZ001


def test_hour_floor():
    dt = datetime(2026, 8, 19, 10, 59, 59, 999999, tzinfo=UTC)
    assert hour_floor(dt) == datetime(2026, 8, 19, 10, 0, tzinfo=UTC)


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


def test_prev_hour_range():
    dt = datetime(2026, 8, 19, 10, 2, tzinfo=UTC)
    start, end = prev_hour_range(dt)
    assert start == datetime(2026, 8, 19, 9, 0, tzinfo=UTC)
    assert end == datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
