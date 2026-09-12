"""购买模式与计费周期:折扣的唯一计算点(市场页报价 / 创建预估 / 实例落库快照 / 续费共用)。
`market`(按量 / 竞价 / 包周期)与 `skus.tier` 正交。"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from app.core.money import as_amount, as_price, billing_units
from app.core.platform_config import RuntimeConfig

# 购买模式(instances.market)
MARKET_ON_DEMAND = "on_demand"  # 按量:小时结算,唯一进 bills_hourly 的模式
MARKET_SPOT = "spot"  # 竞价:折扣价 + 可被平台回收
MARKET_SUBSCRIPTION = "subscription"  # 包周期:下单一次性预扣,小时结算跳过

# 计费周期(subscriptions.period)
PERIOD_DAY = "day"
PERIOD_WEEK = "week"
PERIOD_MONTH = "month"
PERIOD_YEAR = "year"

# 周期长度取定长小时(不取自然月),定价与到期时刻同源;见 docs/reference/billing.md
PERIOD_HOURS: dict[str, int] = {
    PERIOD_DAY: 24,
    PERIOD_WEEK: 24 * 7,
    PERIOD_MONTH: 24 * 30,
    PERIOD_YEAR: 24 * 365,
}

# 单次下单/续费的周期数上限
MAX_PERIOD_COUNT = 36


def period_hours(period: str, count: int = 1) -> int:
    """周期总小时数;未知周期或非正数量抛 ValueError。"""
    if period not in PERIOD_HOURS:
        raise ValueError(f"unknown period: {period!r}")
    if not 1 <= count <= MAX_PERIOD_COUNT:
        raise ValueError(f"period_count out of range: {count}")
    return PERIOD_HOURS[period] * count


def period_delta(period: str, count: int = 1) -> timedelta:
    """周期时长(与 period_hours 同源)。"""
    return timedelta(hours=period_hours(period, count))


def period_discount_pct(policies: RuntimeConfig, period: str) -> int:
    """周期折扣(百分数,80 = 8 折);未知周期抛 ValueError。"""
    match period:
        case "day":
            return policies.period_discount_day
        case "week":
            return policies.period_discount_week
        case "month":
            return policies.period_discount_month
        case "year":
            return policies.period_discount_year
    raise ValueError(f"unknown period: {period!r}")


def price_for(
    base_hourly: Decimal,
    *,
    market: str,
    policies: RuntimeConfig,
    period: str | None = None,
) -> Decimal:
    """该购买模式下的有效时价(4 位小数),即 `instances.price_hourly`;base_hourly 为 SKU 原价。"""
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
    """包周期下单/续费报价;`discount_amount == list_amount - amount`。"""

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
    policies: RuntimeConfig,
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
