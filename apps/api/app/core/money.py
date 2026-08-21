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

    定价模型是 development-plan §「GB·月单价 / 30」(与同类产品同口径),所以 31 天的月份
    用户实际付 31/30 = 103.3% 的名义月费 —— 这是设计,不是偏差。

    但**逐日单独舍到分**是实打实的偏差:日费量级极小(10GB 只有 1.17 分),半分的舍入
    误差相对日费可达 ±14%,而且每天都朝同一个方向舍,按月累积不抵消。默认单价下:
    10GB 名义月费 0.35,逐日舍分后 ×30 天只收到 0.30(−14.3%);30GB 的日费 raw 恰好
    是 0.035 这个分位 tie,HALF_EVEN 向偶进到 0.04,×31 天收到 1.24 对 1.05(+18%)。
    对一个把「元/GB·月」印在市场页上的平台,「按月单价换算不回去」是可被用户拿出来说的。

    修法是累积差分:第 k 天扣的是「前 k 天累计应收」减「前 k-1 天累计应收」,两端都只在
    出账那一刻舍一次。于是整月累计恒等于 as_amount(月费 × 当月天数 / 30),而每日仍然
    是整分入账(amount 列不必改 scale),且仍是 (price, size, day) 的纯函数 ——
    UNIQUE(disk_id, day) 幂等、补账循环、水位线追平重跑,结果全都一致。

    day 省略时退化为「均摊日费」(展示与余额预估用,不作为入账口径)。
    """
    if size_gb < 0:
        raise ValueError(f"size_gb out of range: {size_gb}")
    monthly = as_price(price_gb_month) * Decimal(size_gb)
    if day is None:
        return as_amount(monthly / Decimal(30))
    k = Decimal(day.day)
    return as_amount(monthly * k / Decimal(30)) - as_amount(monthly * (k - 1) / Decimal(30))
