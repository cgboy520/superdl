"""Currencies a deployment may settle in, with their ISO 4217 minor-unit exponent.

Three-decimal currencies (KWD, BHD, …) are deliberately absent: amounts are stored as
numeric(…, 2), so only 0- and 2-decimal currencies fit without a column rewrite.
"""

CURRENCY_MINOR_UNITS: dict[str, int] = {
    "AUD": 2,
    "BRL": 2,
    "CAD": 2,
    "CHF": 2,
    "CNY": 2,
    "EUR": 2,
    "GBP": 2,
    "HKD": 2,
    "INR": 2,
    "JPY": 0,
    "KRW": 0,
    "MXN": 2,
    "SGD": 2,
    "TWD": 2,
    "USD": 2,
    "VND": 0,
}
SUPPORTED_CURRENCIES: frozenset[str] = frozenset(CURRENCY_MINOR_UNITS)
