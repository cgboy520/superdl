"""Accept-Language negotiation: q-values, header order, primary subtag match, default fallback."""

import pytest

from app.core.locale import DEFAULT_LOCALE, negotiate_locale


@pytest.mark.parametrize(
    "header, expected",
    [
        (None, "en-US"),
        ("", "en-US"),
        ("zh-CN,zh;q=0.9,en;q=0.8", "zh-CN"),
        ("en-GB,en;q=0.9", "en-US"),
        ("fr-FR, de", "en-US"),
        ("en;q=0.5, zh;q=0.9", "zh-CN"),
        ("zh-TW", "zh-CN"),
        ("zh-Hans-CN", "zh-CN"),
        ("*", "en-US"),
        ("fr, *;q=0.1, zh;q=0.5", "zh-CN"),
        ("zh;q=0, en", "en-US"),
        ("ZH , en", "zh-CN"),
    ],
)
def test_negotiate(header: str | None, expected: str):
    assert negotiate_locale(header) == expected


def test_default_is_english():
    assert DEFAULT_LOCALE == "en-US"
