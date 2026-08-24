from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.core.timeutil import day_floor, ensure_utc, hour_floor, prev_hour_range


def test_ensure_utc_rejects_naive():
    with pytest.raises(ValueError):
        ensure_utc(datetime(2026, 1, 1))  # noqa: DTZ001


def test_ensure_utc_converts_offset():
    cst = timezone(timedelta(hours=8))
    dt = datetime(2026, 8, 19, 10, 30, tzinfo=cst)
    assert ensure_utc(dt) == datetime(2026, 8, 19, 2, 30, tzinfo=UTC)


def test_hour_floor():
    dt = datetime(2026, 8, 19, 10, 59, 59, 999999, tzinfo=UTC)
    assert hour_floor(dt) == datetime(2026, 8, 19, 10, 0, tzinfo=UTC)


def test_day_floor():
    dt = datetime(2026, 8, 19, 23, 1, tzinfo=UTC)
    assert day_floor(dt) == datetime(2026, 8, 19, 0, 0, tzinfo=UTC)


def test_prev_hour_range():
    dt = datetime(2026, 8, 19, 10, 2, tzinfo=UTC)
    start, end = prev_hour_range(dt)
    assert start == datetime(2026, 8, 19, 9, 0, tzinfo=UTC)
    assert end == datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
