"""Login handles: an email address or an E.164 phone number.

`parse_handle` classifies and normalizes a raw string; the pydantic aliases at the bottom apply the
same normalization inside request schemas. Masking and rate-limit key derivation live here so every
caller shapes handles identically.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import AfterValidator, Field

E164_RE = r"^\+[1-9][0-9]{6,14}$"
EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
EMAIL_MAX_LEN = 254
_RATELIMIT_PLAIN_MAX = 48

_E164 = re.compile(E164_RE)
_EMAIL = re.compile(EMAIL_RE)
_PHONE_LIKE = re.compile(r"^\+?[0-9]{7,15}$")
_PHONE_MASK_MIN_LEN = 11
_PHONE_SEPARATORS = re.compile(r"[\s\-().]")

HandleKind = Literal["email", "phone"]


@dataclass(frozen=True)
class Handle:
    kind: HandleKind
    value: str


def normalize_email(raw: str) -> str:
    """Trim and lower-case; reject anything that is not a single `local@domain.tld` token."""
    value = raw.strip().lower()
    if len(value) > EMAIL_MAX_LEN or not _EMAIL.match(value):
        raise ValueError("invalid email address")
    return value


def normalize_phone(raw: str) -> str:
    """Strip separators (space, dash, dot, parentheses); require E.164: `+` and 7–15 digits."""
    value = _PHONE_SEPARATORS.sub("", raw.strip())
    if not _E164.match(value):
        raise ValueError("phone must be in E.164 format, e.g. +14155550123")
    return value


def parse_handle(raw: str) -> Handle:
    """Classify by shape: `@` means email, a leading `+` means phone; anything else is rejected."""
    stripped = raw.strip()
    if "@" in stripped:
        return Handle("email", normalize_email(stripped))
    if stripped.startswith("+"):
        return Handle("phone", normalize_phone(stripped))
    raise ValueError("handle must be an email address or an E.164 phone number")


def mask_handle(value: str | None) -> str:
    """Valid email → first local-part character + `***@domain`; phone (E.164 or bare digits, at
    least 11 characters) → first 3 + `****` + last 4; anything else or empty → `******`."""
    if not value:
        return "******"
    if _EMAIL.match(value) and len(value) <= EMAIL_MAX_LEN:
        local, _, domain = value.partition("@")
        return f"{local[:1]}***@{domain}"
    if _PHONE_LIKE.match(value) and len(value) >= _PHONE_MASK_MIN_LEN:
        return f"{value[:3]}****{value[-4:]}"
    return "******"


def ratelimit_key(handle: str) -> str:
    """Handle component of a rate-limit key; long values are hashed so `prefix:ip:handle` stays
    inside the 128-character counter key even for maximal-length emails."""
    if len(handle) <= _RATELIMIT_PLAIN_MAX:
        return handle
    return "h:" + hashlib.sha256(handle.encode()).hexdigest()[:32]


def _normalize_handle(raw: str) -> str:
    return parse_handle(raw).value


NormalizedEmail = Annotated[
    str,
    AfterValidator(normalize_email),
    Field(
        max_length=EMAIL_MAX_LEN,
        description="Email address; trimmed and lower-cased",
        examples=["user@example.com"],
    ),
]
NormalizedPhone = Annotated[
    str,
    AfterValidator(normalize_phone),
    Field(
        description="E.164 phone number; spaces, dashes, dots and parentheses are stripped",
        examples=["+14155550123"],
    ),
]
NormalizedHandle = Annotated[
    str,
    AfterValidator(_normalize_handle),
    Field(
        max_length=EMAIL_MAX_LEN,
        description="Login handle: an email address or an E.164 phone number",
        examples=["user@example.com", "+14155550123"],
    ),
]
