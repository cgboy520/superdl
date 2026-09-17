"""Purchase modes and billing periods: the single point for discounts (market quotes / creation
estimates / instance snapshot / renewal).
`market` (on-demand / spot / subscription) is orthogonal to `skus.tier`."""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from app.core.money import as_amount, as_price, billing_units
from app.core.platform_config import RuntimeConfig

MARKET_ON_DEMAND = "on_demand"
MARKET_SPOT = "spot"
MARKET_SUBSCRIPTION = "subscription"

PERIOD_DAY = "day"
PERIOD_WEEK = "week"
PERIOD_MONTH = "month"
PERIOD_YEAR = "year"

PERIOD_HOURS: dict[str, int] = {
    PERIOD_DAY: 24,
    PERIOD_WEEK: 24 * 7,
    PERIOD_MONTH: 24 * 30,
    PERIOD_YEAR: 24 * 365,
}

MAX_PERIOD_COUNT = 36


def period_hours(period: str, count: int = 1) -> int:
    """Fixed hours per period (a month is 30 days, a year 365); ValueError for an unknown period or
    a count outside 1..36."""
    if period not in PERIOD_HOURS:
        raise ValueError(f"unknown period: {period!r}")
    if not 1 <= count <= MAX_PERIOD_COUNT:
        raise ValueError(f"period_count out of range: {count}")
    return PERIOD_HOURS[period] * count


def period_delta(period: str, count: int = 1) -> timedelta:
    return timedelta(hours=period_hours(period, count))


def period_discount_pct(policies: RuntimeConfig, period: str) -> int:
    """Period discount (percent, 80 = 20 % off); ValueError for an unknown period."""
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
    """Effective hourly price (4 dp) under the purchase mode, i.e. `instances.price_hourly`;
    base_hourly is the SKU list price."""
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
    """Subscription order / renewal quote; `discount_amount == list_amount - amount`."""

    period: str
    period_count: int
    hours: int
    discount_pct: int
    base_hourly: Decimal
    unit_price: Decimal
    list_amount: Decimal
    discount_amount: Decimal
    amount: Decimal


def quote_subscription(
    base_hourly: Decimal,
    *,
    gpu_count: int,
    period: str,
    period_count: int,
    policies: RuntimeConfig,
) -> SubscriptionQuote:
    """Subscription quote. gpu_count is converted to units by billing_units (CPU instances always
    1)."""
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
