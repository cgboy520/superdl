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
    """数据盘日结:GB·月单价 / 30 × 容量。返回该「盘×日」应扣的 2 位小数金额。

    31 天的月份实际收 31/30 的名义月费,这是定价模型本身,不要「修正」。

    必须用累积差分:第 k 天扣的是「前 k 天累计应收」减「前 k-1 天累计应收」,两端只在出账
    那一刻各舍一次。改成逐日单独舍到分会朝同一方向累积误差(默认单价下整月可达 ±14%);
    累积差分让整月累计恒等于 as_amount(月费 × 当月天数 / 30),且仍是 (price, size, day)
    的纯函数,幂等重跑与补账结果一致。

    day 省略时退化为「均摊日费」(展示与余额预估用,不作为入账口径)。
    """
    if size_gb < 0:
        raise ValueError(f"size_gb out of range: {size_gb}")
    monthly = as_price(price_gb_month) * Decimal(size_gb)
    if day is None:
        return as_amount(monthly / Decimal(30))
    k = Decimal(day.day)
    return as_amount(monthly * k / Decimal(30)) - as_amount(monthly * (k - 1) / Decimal(30))
