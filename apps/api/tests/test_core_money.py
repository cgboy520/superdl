from decimal import Decimal

import pytest

from app.core.money import as_amount, as_price, disk_daily_charge, hourly_charge


class TestPriceQuantize:
    def test_price_four_decimals(self):
        assert as_price("1.5") == Decimal("1.5000")
        assert as_price("0.12345") == Decimal("0.1234")  # HALF_EVEN: 0.12345 → 0.1234

    def test_amount_two_decimals_half_even(self):
        assert as_amount("1.005") == Decimal("1.00")  # 半舍向偶:1.005 → 1.00
        assert as_amount("1.015") == Decimal("1.02")  # 1.015 → 1.02
        assert as_amount("1.025") == Decimal("1.02")  # 1.025 → 1.02

    def test_float_forbidden(self):
        with pytest.raises(TypeError):
            as_price(1.5)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            as_amount(0.1)  # type: ignore[arg-type]


class TestHourlyCharge:
    def test_full_hour_equals_unit_price(self):
        assert hourly_charge(Decimal("2.5000"), 3600) == Decimal("2.50")

    def test_partial_hour(self):
        # 1.68/时 × 1800s = 0.84
        assert hourly_charge(Decimal("1.6800"), 1800) == Decimal("0.84")

    def test_zero_seconds(self):
        assert hourly_charge(Decimal("9.9900"), 0) == Decimal("0.00")

    def test_one_second_rounds(self):
        # 3.6/时 × 1s = 0.001 → HALF_EVEN → 0.00
        assert hourly_charge(Decimal("3.6000"), 1) == Decimal("0.00")

    def test_out_of_range(self):
        with pytest.raises(ValueError):
            hourly_charge(Decimal("1"), 3601)
        with pytest.raises(ValueError):
            hourly_charge(Decimal("1"), -1)


class TestDiskDailyCharge:
    def test_basic(self):
        # 0.03 元/GB·月 × 100GB / 30 = 0.10/日
        assert disk_daily_charge(Decimal("0.0300"), 100) == Decimal("0.10")

    def test_rounding(self):
        # 0.035 × 100 / 30 = 0.11666... → 0.12
        assert disk_daily_charge(Decimal("0.0350"), 100) == Decimal("0.12")
