"""Mainland China: mobile number shape, resident ID number (GB 11643) and unified social credit
code (GB 32100) validators."""

import re

from app.core.regions.base import RegionRules

DIAL_CODE = "86"
NATIONAL_PHONE_RE = r"^1[3-9]\d{9}$"
NATIONAL_PHONE_RE_LOOSE = r"^1\d{10}$"
ID_NUMBER_RE = r"^\d{17}[\dX]$"
USCC_RE = r"^[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}$"

_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK_CHARS = "10X98765432"
_ID_NUMBER_PATTERN = re.compile(ID_NUMBER_RE)


def validate_id_number(value: str) -> bool:
    """18-character resident ID: shape plus the ISO 7064 MOD 11-2 check character (`x` accepted)."""
    candidate = value.strip().upper()
    if not _ID_NUMBER_PATTERN.match(candidate):
        return False
    total = sum(int(d) * w for d, w in zip(candidate[:17], _ID_WEIGHTS, strict=True))
    return _ID_CHECK_CHARS[total % 11] == candidate[17]


RULES = RegionRules(
    iso="CN",
    dial_code=DIAL_CODE,
    national_phone_re=NATIONAL_PHONE_RE,
    validate_id_number=validate_id_number,
    tax_id_re=USCC_RE,
)
