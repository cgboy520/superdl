"""Locale negotiation for server-rendered text (verification-code templates, emails).

API error bodies are not negotiated: `message` is always English and clients translate by
`message_key`.
"""

import re
from typing import Literal

Locale = Literal["en-US", "zh-CN"]
SUPPORTED_LOCALES: tuple[Locale, ...] = ("en-US", "zh-CN")
DEFAULT_LOCALE: Locale = "en-US"

_PRIMARY_TO_LOCALE: dict[str, Locale] = {"en": "en-US", "zh": "zh-CN"}
_Q_RE = re.compile(r";\s*q\s*=\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)


def _ranked_entry(part: str, position: int) -> tuple[float, int, str] | None:
    tag, _, params = part.strip().partition(";")
    if not tag:
        return None
    match = _Q_RE.search(";" + params)
    q = float(match.group(1)) if match else 1.0
    return (-q, position, tag.lower())


def _locale_of(tag: str) -> Locale | None:
    return _PRIMARY_TO_LOCALE.get(tag.split("-", 1)[0])


def negotiate_locale(accept_language: str | None) -> Locale:
    """Best supported locale for an Accept-Language header (q-values, then header order).

    Matching is by primary subtag, so `zh-TW` and `zh-Hans` resolve to zh-CN; `q=0` excludes a
    language; `*` picks the first supported locale not excluded; unknown languages, empty or
    unparseable input yield the default."""
    if not accept_language:
        return DEFAULT_LOCALE
    entries = (_ranked_entry(part, i) for i, part in enumerate(accept_language.split(",")))
    ranked = sorted(e for e in entries if e is not None)
    excluded = {loc for q, _, tag in ranked if q == 0 and (loc := _locale_of(tag)) is not None}
    for q, _, tag in ranked:
        if q == 0:
            break
        if tag == "*":
            for candidate in SUPPORTED_LOCALES:
                if candidate not in excluded:
                    return candidate
            return DEFAULT_LOCALE
        locale = _locale_of(tag)
        if locale is not None:
            return locale
    return DEFAULT_LOCALE
