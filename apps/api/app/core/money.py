"""Money helpers: unit prices carry 4 decimals (numeric(12,4)); amounts are quantized to the
currency's minor unit (numeric(14,2) storage; 0- or 2-decimal currencies), ROUND_HALF_EVEN.
Server-rendered text uses `money_label` / `price_label` ("100.00 CNY"), never a symbol."""

from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Annotated

from pydantic import PlainSerializer

from app.core.config import get_settings
from app.core.currencies import CURRENCY_MINOR_UNITS


def money_str(v: Decimal) -> str:
    """Decimal → string (scale preserved, no scientific notation)."""
    return format(v, "f")


MoneyOut = Annotated[Decimal, PlainSerializer(money_str, return_type=str, when_used="json")]

PRICE_QUANT = Decimal("0.0001")


def platform_currency() -> str:
    """ISO 4217 code the deployment settles in (`SUPERDL_PLATFORM_CURRENCY`)."""
    return get_settings().platform_currency


def minor_units(currency: str | None = None) -> int:
    """Decimal places of the currency (default: the platform currency)."""
    return CURRENCY_MINOR_UNITS[currency or platform_currency()]


def as_price(value: Decimal | str | int) -> Decimal:
    """Normalise to a 4-dp unit price. float is rejected outright."""
    if isinstance(value, float):
        raise TypeError("float is forbidden for money")
    return Decimal(value).quantize(PRICE_QUANT, rounding=ROUND_HALF_EVEN)


def amount_quant(currency: str | None = None) -> Decimal:
    """Quantum of the currency's minor unit: 0.01 for two-decimal currencies, 1 for zero-decimal."""
    return Decimal(1).scaleb(-minor_units(currency))


def as_amount(value: Decimal | str | int, *, currency: str | None = None) -> Decimal:
    """Amount quantized to the currency's minor unit (default: platform currency), HALF_EVEN."""
    if isinstance(value, float):
        raise TypeError("float is forbidden for money")
    return Decimal(value).quantize(amount_quant(currency), rounding=ROUND_HALF_EVEN)


def money_label(value: Decimal | str | int, currency: str | None = None) -> str:
    """Amount with its ISO code for server-rendered text, e.g. "100.00 CNY"."""
    code = currency or platform_currency()
    return f"{money_str(as_amount(value, currency=code))} {code}"


def price_label(value: Decimal | str | int, currency: str | None = None) -> str:
    """Four-decimal unit price with its ISO code, e.g. "1.2345 USD"."""
    code = currency or platform_currency()
    return f"{money_str(as_price(value))} {code}"


def billing_units(gpu_count: int) -> int:
    """How many `price_hourly` units an hour charges: GPU SKUs charge N per-card units, CPU SKUs one
    whole-machine unit."""
    if gpu_count < 0:
        raise ValueError(f"gpu_count out of range: {gpu_count}")
    return gpu_count or 1


def hourly_cost(price_hourly: Decimal, gpu_count: int) -> Decimal:
    """Instance hourly cost (2-dp posting) = unit price × billing units."""
    return as_amount(as_price(price_hourly) * billing_units(gpu_count))


def disk_daily_charge(price_gb_month: Decimal, size_gb: int, day: date | None = None) -> Decimal:
    """Daily data-disk settlement amount (2 dp): day k = as_amount(monthly × k / 30) -
    as_amount(monthly × (k-1) / 30).
    Without day the flat daily fee is returned, for display and estimates only."""
    if size_gb < 0:
        raise ValueError(f"size_gb out of range: {size_gb}")
    monthly = as_price(price_gb_month) * Decimal(size_gb)
    if day is None:
        return as_amount(monthly / Decimal(30))
    k = Decimal(day.day)
    return as_amount(monthly * k / Decimal(30)) - as_amount(monthly * (k - 1) / Decimal(30))
