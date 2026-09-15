"""金额统一入口:单价 4 位小数(numeric(12,4)),入账 2 位小数(numeric(14,2)),ROUND_HALF_EVEN。"""

from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Annotated

from pydantic import PlainSerializer


def money_str(v: Decimal) -> str:
    """Decimal → 字符串(保 scale,不科学计数)。"""
    return format(v, "f")


MoneyOut = Annotated[Decimal, PlainSerializer(money_str, return_type=str, when_used="json")]

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


def billing_units(gpu_count: int) -> int:
    """一小时收几份 `price_hourly`:GPU SKU 单卡时价收 N 份,CPU SKU 整机时价收 1 份。"""
    if gpu_count < 0:
        raise ValueError(f"gpu_count out of range: {gpu_count}")
    return gpu_count or 1


def hourly_cost(price_hourly: Decimal, gpu_count: int) -> Decimal:
    """实例时费(2 位入账口径)= 单价 × 计费份数。"""
    return as_amount(as_price(price_hourly) * billing_units(gpu_count))


def disk_daily_charge(price_gb_month: Decimal, size_gb: int, day: date | None = None) -> Decimal:
    """数据盘日结金额(2 位小数):第 k 天 = as_amount(月费 × k / 30) - as_amount(月费 × (k-1) / 30)。
    day 省略时返回均摊日费,仅供展示与预估。"""
    if size_gb < 0:
        raise ValueError(f"size_gb out of range: {size_gb}")
    monthly = as_price(price_gb_month) * Decimal(size_gb)
    if day is None:
        return as_amount(monthly / Decimal(30))
    k = Decimal(day.day)
    return as_amount(monthly * k / Decimal(30)) - as_amount(monthly * (k - 1) / Decimal(30))
