"""Login-handle normalization, classification, masking and rate-limit key derivation."""

import pytest
from pydantic import BaseModel, ValidationError

from app.core.handles import (
    Handle,
    NormalizedEmail,
    NormalizedHandle,
    NormalizedPhone,
    mask_handle,
    normalize_email,
    normalize_phone,
    parse_handle,
    ratelimit_key,
)


class TestNormalizeEmail:
    def test_trims_and_lowercases(self):
        assert normalize_email("  Alice@Example.COM ") == "alice@example.com"

    @pytest.mark.parametrize("raw", ["alice", "alice@", "@example.com", "a b@example.com", "a@b"])
    def test_rejects_malformed(self, raw: str):
        with pytest.raises(ValueError):
            normalize_email(raw)

    def test_rejects_over_254_chars(self):
        with pytest.raises(ValueError):
            normalize_email("a" * 243 + "@example.com")


class TestNormalizePhone:
    def test_strips_separators(self):
        assert normalize_phone(" +1 (415) 555-0123 ") == "+14155550123"
        assert normalize_phone("+86.138.0000.1111") == "+8613800001111"

    @pytest.mark.parametrize(
        "raw",
        [
            "13800001111",
            "+0123456789",
            "+123456",
            "+1234567890123456",
            "+1415abc0123",
            "+1١٢٣٤٥٦٧",  # Arabic-Indic digits are not E.164 digits
        ],
    )
    def test_rejects_non_e164(self, raw: str):
        with pytest.raises(ValueError):
            normalize_phone(raw)


class TestParseHandle:
    def test_email_and_phone_classified_by_shape(self):
        assert parse_handle("Bob@Example.org") == Handle("email", "bob@example.org")
        assert parse_handle("+86 138 0000 1111") == Handle("phone", "+8613800001111")

    @pytest.mark.parametrize("raw", ["13800001111", "bob", "", "   "])
    def test_bare_digits_and_garbage_rejected(self, raw: str):
        with pytest.raises(ValueError):
            parse_handle(raw)


class TestMaskHandle:
    def test_email_keeps_first_char_and_domain(self):
        assert mask_handle("alice@example.com") == "a***@example.com"

    def test_phone_keeps_first_3_and_last_4(self):
        assert mask_handle("+8613800001111") == "+86****1111"
        assert mask_handle("13800001111") == "138****1111"

    @pytest.mark.parametrize(
        "value",
        [None, "", "not-a-handle", "del:42:abcd", "1234567", "+123456789", "@secret", "a@b"],
    )
    def test_everything_else_is_fully_masked(self, value: str | None):
        """Short phones and malformed emails must not leak their digits or suffix."""
        assert mask_handle(value) == "******"


class TestRatelimitKey:
    def test_short_handles_pass_through(self):
        assert ratelimit_key("alice@example.com") == "alice@example.com"
        assert ratelimit_key("+8613800001111") == "+8613800001111"

    def test_long_handles_are_hashed_to_a_bounded_length(self):
        long_email = "a" * 240 + "@example.com"
        key = ratelimit_key(long_email)
        assert key.startswith("h:") and len(key) == 34
        assert key != ratelimit_key("b" * 240 + "@example.com")


class _Body(BaseModel):
    email: NormalizedEmail | None = None
    phone: NormalizedPhone | None = None
    handle: NormalizedHandle | None = None


class TestPydanticAliases:
    def test_fields_normalize(self):
        body = _Body(email=" X@Y.io ", phone="+1 415 555 0123", handle="+86-138-0000-1111")
        assert (body.email, body.phone, body.handle) == ("x@y.io", "+14155550123", "+8613800001111")

    @pytest.mark.parametrize(
        "field, value", [("email", "nope"), ("phone", "0123"), ("handle", "1")]
    )
    def test_fields_reject_invalid(self, field: str, value: str):
        with pytest.raises(ValidationError):
            _Body(**{field: value})
