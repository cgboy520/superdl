"""金额统一入口:单价 4 位小数(numeric(12,4)),入账 2 位小数(numeric(14,2)),舍入 ROUND_HALF_EVEN。"""

from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Annotated

from pydantic import PlainSerializer

# API 出参金额一律序列化为字符串(保 scale、避免 float);前端按字符串渲染
MoneyOut = Annotated[
    Decimal, PlainSerializer(lambda v: format(v, "f"), return_type=str, when_used="json")
]

PRICE_QUANT = Decimal("0.0001")
AMOUNT_QUANT = Decimal("0.01")


def as_price(value: Decimal | str | int) -> Decimal:
    """规整为 4 位小数单价。float 直接拒绝。"""
    if isinstance(value, float):
        raise TypeError("float is forbidden for money")
    return Decimal(value).quantize(PRICE_QUANT, rounding=ROUND_HALF_EVEN)


def as_amount(value: Decimal | str | int) -> Decimal:
    """规整为 2 位小数入账金额,ROUND_HALF_EVEN。"""
    if isinstance(value, float):
        raise TypeError("float is forbidden for money")
    return Decimal(value).quantize(AMOUNT_QUANT, rounding=ROUND_HALF_EVEN)


def disk_daily_charge(price_gb_month: Decimal, size_gb: int, day: date | None = None) -> Decimal:
    """数据盘日结:GB·月单价 / 30 × 容量,返回该「盘×日」应扣的 2 位小数金额。

    口径为累积差分:第 k 天 = as_amount(月费 × k / 30) - as_amount(月费 × (k-1) / 30),
    整月累计恒等于 as_amount(月费 × 当月天数 / 30)。逐日单独舍入会朝同方向累积误差。
    31 天的月份按名义月费的 31/30 收取,属定价模型。

    day 省略时返回均摊日费,仅用于展示与余额预估,不作入账口径。
    """
    if size_gb < 0:
        raise ValueError(f"size_gb out of range: {size_gb}")
    monthly = as_price(price_gb_month) * Decimal(size_gb)
    if day is None:
        return as_amount(monthly / Decimal(30))
    k = Decimal(day.day)
    return as_amount(monthly * k / Decimal(30)) - as_amount(monthly * (k - 1) / Decimal(30))
