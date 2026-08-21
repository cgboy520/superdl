from datetime import date
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
        # 不带 day = 均摊日费(展示与余额预估口径):0.03 元/GB·月 × 100GB / 30 = 0.10/日
        assert disk_daily_charge(Decimal("0.0300"), 100) == Decimal("0.10")

    def test_rounding(self):
        # 0.035 × 100 / 30 = 0.11666... → 0.12
        assert disk_daily_charge(Decimal("0.0350"), 100) == Decimal("0.12")

    @pytest.mark.parametrize("year,month,days", [(2026, 2, 28), (2026, 4, 30), (2026, 7, 31)])
    @pytest.mark.parametrize("size_gb", [10, 30, 100, 500, 4096])
    def test_month_total_matches_list_price(self, year, month, days, size_gb):
        """整月累计 == 名义月费 × 当月天数 / 30,一分不差。

        逐日单独舍分时不成立:日费量级极小,半分误差相对日费可达 ±14%,且每天朝同一
        方向舍、按月累积不抵消。默认单价 0.035 下,10GB 逐日舍分后整月只收到 0.30 而名义
        月费 0.35(−14.3%);30GB 的日费 raw 恰是分位 tie,HALF_EVEN 向偶后 31 天多收 18%。
        """
        price = Decimal("0.0350")
        total = sum(
            (disk_daily_charge(price, size_gb, date(year, month, d)) for d in range(1, days + 1)),
            Decimal("0.00"),
        )
        assert total == as_amount(price * size_gb * days / 30)

    def test_daily_amount_never_negative(self):
        """差分不能算出负数 —— 否则 DB 的 amount >= 0 约束会拦住结算。"""
        price = Decimal("0.0350")
        for d in range(1, 32):
            assert disk_daily_charge(price, 10, date(2026, 7, d)) >= 0

    def test_pure_function_of_day(self):
        """(price, size, day) 的纯函数:UNIQUE(disk_id, day) 幂等与补账重跑都依赖这一点。"""
        a = disk_daily_charge(Decimal("0.0350"), 100, date(2026, 7, 17))
        b = disk_daily_charge(Decimal("0.0350"), 100, date(2026, 7, 17))
        assert a == b
