"""Billing calendar in the deployment zone (DST-safe day/period boundaries and stepping) plus the
fixed-offset client helpers."""

from datetime import UTC, date, datetime

import pytest

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.core.timeutil import (
    billing_day_floor,
    billing_day_range,
    billing_day_shift,
    billing_local_date,
    billing_month_range,
    billing_offset_minutes,
    billing_period,
    billing_period_range,
    ensure_utc,
    local_day_range,
    parse_local_date,
)


@pytest.fixture
def zone(monkeypatch):
    def _set(name: str) -> None:
        monkeypatch.setattr(get_settings(), "billing_timezone", name)

    return _set


def test_ensure_utc_rejects_naive():
    with pytest.raises(ValueError):
        ensure_utc(datetime(2026, 1, 1))  # noqa: DTZ001


def test_billing_month_range_fixed_offset():
    """Client-offset month window: December rolls into the next year; bad format → VALIDATION."""
    assert billing_month_range("2026-08", tz_offset_minutes=480) == (
        datetime(2026, 7, 31, 16, 0, tzinfo=UTC),
        datetime(2026, 8, 31, 16, 0, tzinfo=UTC),
    )
    assert billing_month_range("2026-12", tz_offset_minutes=-300) == (
        datetime(2026, 12, 1, 5, 0, tzinfo=UTC),
        datetime(2027, 1, 1, 5, 0, tzinfo=UTC),
    )
    for bad in ("2026-13", "2026/08", "bad"):
        with pytest.raises(AppError) as exc:
            billing_month_range(bad, tz_offset_minutes=0)
        assert exc.value.code is ErrorCode.VALIDATION_ERROR


def test_local_day_range():
    at = datetime(2026, 8, 19, 3, 30, tzinfo=UTC)
    start, end = local_day_range(480, at=at)
    assert start == datetime(2026, 8, 18, 16, 0, tzinfo=UTC)
    assert end == datetime(2026, 8, 19, 16, 0, tzinfo=UTC)


def test_parse_local_date():
    start, end = parse_local_date("2026-08-19", 480)
    assert start == datetime(2026, 8, 18, 16, 0, tzinfo=UTC)
    assert end == datetime(2026, 8, 19, 16, 0, tzinfo=UTC)
    with pytest.raises(AppError) as exc:
        parse_local_date("2026/08/19", 480)
    assert exc.value.code is ErrorCode.VALIDATION_ERROR


def test_billing_day_floor_and_shift_across_dst(zone):
    """America/New_York: the spring-forward day is 23 h, the fall-back day 25 h; shifting by
    calendar days lands on local midnight either way, so windows never drift."""
    zone("America/New_York")
    spring = billing_day_floor(datetime(2026, 3, 8, 12, 0, tzinfo=UTC))
    assert spring == datetime(2026, 3, 8, 5, 0, tzinfo=UTC)
    nxt = billing_day_shift(spring, 1)
    assert nxt == datetime(2026, 3, 9, 4, 0, tzinfo=UTC)
    assert (nxt - spring).total_seconds() == 23 * 3600
    fall = billing_day_floor(datetime(2026, 11, 1, 12, 0, tzinfo=UTC))
    assert (billing_day_shift(fall, 1) - fall).total_seconds() == 25 * 3600
    assert billing_day_shift(billing_day_shift(fall, 3), -3) == fall
    assert billing_local_date(datetime(2026, 3, 9, 3, 59, tzinfo=UTC)) == date(2026, 3, 8)
    assert billing_offset_minutes(datetime(2026, 3, 8, 12, 0, tzinfo=UTC)) == -240
    assert billing_offset_minutes(datetime(2026, 1, 8, 12, 0, tzinfo=UTC)) == -300


def test_billing_day_range_and_offsets(zone):
    zone("America/New_York")
    start, end = billing_day_range("2026-03-08")
    assert (start, end) == (
        datetime(2026, 3, 8, 5, 0, tzinfo=UTC),
        datetime(2026, 3, 9, 4, 0, tzinfo=UTC),
    )
    zone("Asia/Kolkata")
    assert billing_offset_minutes(datetime(2026, 6, 1, tzinfo=UTC)) == 330
    assert billing_day_range("2026-06-01")[0] == datetime(2026, 5, 31, 18, 30, tzinfo=UTC)
    for bad in ("2026-3-8", "20260308", "2026/03/08"):
        with pytest.raises(AppError) as exc:
            billing_day_range(bad)
        assert exc.value.code is ErrorCode.VALIDATION_ERROR
    with pytest.raises(AppError) as exc:
        billing_day_range("x", key="adminapi.badDayFormat")
    assert exc.value.message_key == "adminapi.badDayFormat"


def test_billing_period_in_zone(zone):
    """Pacific/Auckland is UTC+13 in January: 2026-01-31T12:00Z already belongs to February."""
    zone("Pacific/Auckland")
    assert billing_period(datetime(2026, 1, 31, 12, 0, tzinfo=UTC)) == "2026-02"
    start, end = billing_period_range("2026-02")
    assert start == datetime(2026, 1, 31, 11, 0, tzinfo=UTC)
    assert end == datetime(2026, 2, 28, 11, 0, tzinfo=UTC)
    zone("Asia/Shanghai")
    assert billing_period_range("2026-12") == (
        datetime(2026, 11, 30, 16, 0, tzinfo=UTC),
        datetime(2026, 12, 31, 16, 0, tzinfo=UTC),
    )
    for bad in ("2026-13", "2026/08", "bad", "2026-8"):
        with pytest.raises(AppError) as exc:
            billing_period_range(bad)
        assert exc.value.code is ErrorCode.VALIDATION_ERROR
