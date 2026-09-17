"""Server-rendered copy: profile locale selection, en-US fallback, explicit locale, placeholder
parity between the two tables."""

# pyright: reportPrivateUsage=false

import string

import pytest

from app.core.config import get_settings
from app.core.servercopy import _COPY, copy


def _placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def test_every_key_has_en_us_and_matching_placeholders():
    """A zh-CN entry with a different placeholder set raises KeyError at render time."""
    for key, table in _COPY.items():
        assert "en-US" in table, key
        for locale, template in table.items():
            assert _placeholders(template) == _placeholders(table["en-US"]), (key, locale)


def test_profile_locale_and_fallback(monkeypatch: pytest.MonkeyPatch):
    """Generic profile stores English, the cn profile stores Chinese; a key without a zh-CN entry
    falls back to English."""
    monkeypatch.setattr(get_settings(), "compliance_profile", None)
    assert copy("billing.remark.recharge", channel="stripe") == "stripe top-up"
    monkeypatch.setattr(get_settings(), "compliance_profile", "cn")
    assert copy("billing.remark.recharge", channel="stripe") == "stripe 充值"  # cjk-ok
    monkeypatch.setitem(_COPY, "test.en_only", {"en-US": "only {x}"})
    assert copy("test.en_only", x=1) == "only 1"


def test_explicit_locale_overrides_profile(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(get_settings(), "compliance_profile", "cn")
    assert copy("billing.remark.recharge", locale="en-US", channel="mock") == "mock top-up"
