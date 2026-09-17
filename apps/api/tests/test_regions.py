"""Region rule registry and the mainland-China validators (phone shape, ID checksum, USCC)."""

import re

import pytest

from app.core.regions import REGIONS, cn, rules_for_dial_code


class TestRegistry:
    def test_lookup_by_dial_code_with_or_without_plus(self):
        assert rules_for_dial_code("+86") is REGIONS["CN"]
        assert rules_for_dial_code("86") is REGIONS["CN"]
        assert rules_for_dial_code("+1") is None
        assert rules_for_dial_code("++86") is None


class TestCnPhone:
    @pytest.mark.parametrize(
        "value, ok", [("13800001111", True), ("12800001111", False), ("1380000111", False)]
    )
    def test_national_shape(self, value: str, ok: bool):
        assert bool(re.match(cn.NATIONAL_PHONE_RE, value)) is ok


class TestCnIdNumber:
    def test_valid_checksum_accepts_upper_and_lower_x(self):
        assert cn.validate_id_number("11010519491231002X")
        assert cn.validate_id_number(" 11010519491231002x ")

    @pytest.mark.parametrize(
        "value",
        [
            "110105194912310021",
            "1101051949123100",
            "11010519491231002XY",
            "abcdefghijklmnopqr",
            "١١٠١٠٥١٩٤٩١٢٣١٠٠٢X",  # Arabic-Indic digits are not GB 11643 digits
        ],
    )
    def test_wrong_check_or_shape_rejected(self, value: str):
        assert not cn.validate_id_number(value)


class TestCnUscc:
    def test_shape(self):
        assert re.match(cn.USCC_RE, "91350100M000100Y43")
        assert not re.match(cn.USCC_RE, "91350100M000100I43")
        assert not re.match(cn.USCC_RE, "91350100M000100Y4")

    def test_rules_expose_validators(self):
        assert cn.RULES.tax_id_re == cn.USCC_RE
        assert cn.RULES.validate_id_number is cn.validate_id_number
