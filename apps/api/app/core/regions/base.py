"""Shared shape of a region's validation rules; region modules build one `RegionRules` each."""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class RegionRules:
    """`validate_id_number` and `tax_id_re` are None when the region defines no such rule."""

    iso: str
    dial_code: str
    national_phone_re: str
    validate_id_number: Callable[[str], bool] | None
    tax_id_re: str | None
