"""金额统一入口:单价 4 位小数(numeric(12,4)),入账 2 位小数(numeric(14,2)),舍入 ROUND_HALF_EVEN。"""

from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Annotated

from pydantic import PlainSerializer


def money_str(v: Decimal) -> str:
    """Decimal → 字符串(保 scale,不科学计数)。API 出参与 CSV 导出共用同一规则。"""
    return format(v, "f")


# API 出参金额一律序列化为字符串(保 scale、避免 float);前端按字符串渲染
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
    """一小时收几份 `price_hourly`。

    `price_hourly` 的语义随 SKU 形态不同:GPU SKU 是**单卡**时价(N 卡实例收 N 份),
    CPU SKU 是**整机**时价(`gpu_count` 恒 0,收 1 份)。

    计费链上所有「单价 × 份数」只经这里换算(bill_amount / 余额护栏 / 燃烧率 / 对账)。
    """
    if gpu_count < 0:
        raise ValueError(f"gpu_count out of range: {gpu_count}")
    return gpu_count or 1


def hourly_cost(price_hourly: Decimal, gpu_count: int) -> Decimal:
    """实例时费(2 位入账口径)= 单价 × 计费份数。"""
    return as_amount(as_price(price_hourly) * billing_units(gpu_count))


def disk_daily_charge(price_gb_month: Decimal, size_gb: int, day: date | None = None) -> Decimal:
    """数据盘日结:GB·月单价 / 30 × 容量,返回该「盘×日」应扣的 2 位小数金额。

    必须按累积差分算(逐日单独舍入会朝同方向累积误差):第 k 天 =
    as_amount(月费 × k / 30) - as_amount(月费 × (k-1) / 30),整月累计恒等于
    as_amount(月费 × 当月天数 / 30)。31 天的月份按名义月费的 31/30 收取,属定价模型。

    day 省略时返回均摊日费,仅用于展示与余额预估,不作入账口径。
    """
    if size_gb < 0:
        raise ValueError(f"size_gb out of range: {size_gb}")
    monthly = as_price(price_gb_month) * Decimal(size_gb)
    if day is None:
        return as_amount(monthly / Decimal(30))
    k = Decimal(day.day)
    return as_amount(monthly * k / Decimal(30)) - as_amount(monthly * (k - 1) / Decimal(30))
