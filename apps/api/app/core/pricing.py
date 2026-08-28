"""购买模式与计费周期:折扣口径的唯一计算点。

`market`(怎么买:按量 / 竞价 / 包周期)与 `skus.tier`(买什么档)正交,一条 SKU 同时供三种模式售卖。

**折扣只在这里算**:市场页报价、创建预估、实例落库快照、续费四处共用同一组函数。
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from app.core.money import as_amount, as_price, billing_units
from app.core.policies import EffectivePolicies

# 购买模式(instances.market)
MARKET_ON_DEMAND = "on_demand"  # 按量:小时结算,唯一进 bills_hourly 的模式
MARKET_SPOT = "spot"  # 竞价:折扣价 + 可被平台回收
MARKET_SUBSCRIPTION = "subscription"  # 包周期:下单一次性预扣,小时结算跳过

# 计费周期(subscriptions.period)
PERIOD_DAY = "day"
PERIOD_WEEK = "week"
PERIOD_MONTH = "month"
PERIOD_YEAR = "year"

# 周期长度取定长小时,不取自然月/自然年:定价与到期时刻必须同源,否则两者分叉。
# 31 天的月份少收一天,属定价模型(同 disk_daily_charge 的「月按 30 天」),
# 见 docs/reference/billing.md。
PERIOD_HOURS: dict[str, int] = {
    PERIOD_DAY: 24,
    PERIOD_WEEK: 24 * 7,
    PERIOD_MONTH: 24 * 30,
    PERIOD_YEAR: 24 * 365,
}

# 周期 → 折扣策略键(EffectivePolicies 上的字段名)
_PERIOD_DISCOUNT_KEYS: dict[str, str] = {
    PERIOD_DAY: "period_discount_day",
    PERIOD_WEEK: "period_discount_week",
    PERIOD_MONTH: "period_discount_month",
    PERIOD_YEAR: "period_discount_year",
}

# 单次下单/续费的周期数上限:period_count 是用户可控的乘数,
# 无上限时应付额可溢出 numeric(14,2)(报 500)
MAX_PERIOD_COUNT = 36


def period_hours(period: str, count: int = 1) -> int:
    """周期总小时数。未知周期或非正数量直接 ValueError(调用方在契约层已拦)。"""
    if period not in PERIOD_HOURS:
        raise ValueError(f"unknown period: {period!r}")
    if not 1 <= count <= MAX_PERIOD_COUNT:
        raise ValueError(f"period_count out of range: {count}")
    return PERIOD_HOURS[period] * count


def period_delta(period: str, count: int = 1) -> timedelta:
    """周期时长。与 period_hours 同源,到期时刻与定价不会各走各的。"""
    return timedelta(hours=period_hours(period, count))


def period_discount_pct(policies: EffectivePolicies, period: str) -> int:
    """周期折扣(百分数,80 = 8 折)。运营在管理端可调。"""
    key = _PERIOD_DISCOUNT_KEYS.get(period)
    if key is None:
        raise ValueError(f"unknown period: {period!r}")
    return int(getattr(policies, key))


def price_for(
    base_hourly: Decimal,
    *,
    market: str,
    policies: EffectivePolicies,
    period: str | None = None,
) -> Decimal:
    """该购买模式下的**有效时价**(4 位小数)。base_hourly 为 SKU 原价。

    `instances.price_hourly` 落的就是这个值,三种模式下含义一致(按量与竞价据此出账,
    包周期不出账但展示与报表按它算)。
    续费的重新定价基准另存在 `subscriptions.unit_price`(原价快照),不从这里反推。
    """
    price = as_price(base_hourly)
    if market == MARKET_SUBSCRIPTION:
        if period is None:
            raise ValueError("subscription price requires a period")
        return as_price(price * period_discount_pct(policies, period) / Decimal(100))
    if market == MARKET_SPOT:
        return as_price(price * policies.spot_discount_pct / Decimal(100))
    return price


@dataclass(frozen=True)
class SubscriptionQuote:
    """一次包周期下单/续费的报价。前端逐行渲染、不自己做乘法(舍入差会对不齐)。

    金额三件套由构造保证自洽:`discount_amount == list_amount - amount`。
    """

    period: str
    period_count: int
    hours: int
    discount_pct: int
    base_hourly: Decimal  # SKU 原价(4 位)
    unit_price: Decimal  # 折后时价(4 位),落 instances.price_hourly
    list_amount: Decimal  # 原价总额(2 位)
    discount_amount: Decimal  # 优惠额(2 位)
    amount: Decimal  # 应付(2 位),即实扣金额


def quote_subscription(
    base_hourly: Decimal,
    *,
    gpu_count: int,
    period: str,
    period_count: int,
    policies: EffectivePolicies,
) -> SubscriptionQuote:
    """包周期报价。gpu_count 经 billing_units 折算份数(CPU 实例恒 1 份)。"""
    hours = period_hours(period, period_count)
    units = billing_units(gpu_count)
    base = as_price(base_hourly)
    unit_price = price_for(base, market=MARKET_SUBSCRIPTION, policies=policies, period=period)
    list_amount = as_amount(base * units * hours)
    amount = as_amount(unit_price * units * hours)
    return SubscriptionQuote(
        period=period,
        period_count=period_count,
        hours=hours,
        discount_pct=period_discount_pct(policies, period),
        base_hourly=base,
        unit_price=unit_price,
        list_amount=list_amount,
        discount_amount=as_amount(list_amount - amount),
        amount=amount,
    )
