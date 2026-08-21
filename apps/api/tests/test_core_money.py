from decimal import Decimal

import pytest

from app.core.money import as_amount, as_price, disk_daily_charge


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


class TestDiskDailyCharge:
    def test_basic(self):
        # 0.03 元/GB·月 × 100GB / 30 = 0.10/日
        assert disk_daily_charge(Decimal("0.0300"), 100) == Decimal("0.10")

    def test_rounding(self):
        # 0.035 × 100 / 30 = 0.11666... → 0.12
        assert disk_daily_charge(Decimal("0.0350"), 100) == Decimal("0.12")
