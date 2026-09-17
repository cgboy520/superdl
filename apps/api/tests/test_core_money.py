"""Currency-aware quantization and labels: minor units per currency, HALF_EVEN ties, ISO-code
labels for server-rendered text."""

from decimal import Decimal

import pytest

from app.core.config import get_settings
from app.core.money import amount_quant, as_amount, as_price, money_label, price_label


def test_amount_quant_follows_minor_units():
    assert amount_quant("USD") == Decimal("0.01")
    assert amount_quant("CNY") == Decimal("0.01")
    assert amount_quant("JPY") == Decimal("1")
    assert amount_quant("KRW") == Decimal("1")


def test_as_amount_quantizes_to_currency_half_even():
    assert as_amount("1.005", currency="USD") == Decimal("1.00")
    assert as_amount("1.015", currency="USD") == Decimal("1.02")
    assert as_amount("1000.5", currency="JPY") == Decimal("1000")
    assert as_amount("1001.5", currency="JPY") == Decimal("1002")
    assert str(as_amount("1000", currency="JPY")) == "1000"
    with pytest.raises(TypeError):
        as_amount(1.0)  # type: ignore[arg-type]


def test_as_amount_defaults_to_platform_currency(monkeypatch):
    monkeypatch.setattr(get_settings(), "platform_currency", "JPY")
    assert as_amount("12.6") == Decimal("13")
    monkeypatch.setattr(get_settings(), "platform_currency", "USD")
    assert as_amount("12.6") == Decimal("12.60")


def test_labels_carry_iso_code_not_symbols(monkeypatch):
    monkeypatch.setattr(get_settings(), "platform_currency", "CNY")
    assert money_label(Decimal("100")) == "100.00 CNY"
    assert money_label("5", "JPY") == "5 JPY"
    assert price_label(Decimal("1.5")) == "1.5000 CNY"
    assert as_price("1.23456") == Decimal("1.2346")
    for label in (money_label("1"), price_label("1")):
        assert "¥" not in label and "$" not in label
