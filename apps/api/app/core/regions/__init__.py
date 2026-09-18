"""Region-specific validation rules, looked up by ISO code or E.164 dial code.

Only mainland China (`CN`, +86) is registered; numbers from other regions get E.164 shape checks
only (see `app.core.handles`).
"""

from app.core.regions import cn
from app.core.regions.base import RegionRules

REGIONS: dict[str, RegionRules] = {"CN": cn.RULES}
_BY_DIAL_CODE = {rules.dial_code: rules for rules in REGIONS.values()}


def rules_for_dial_code(dial_code: str) -> RegionRules | None:
    """Accepts `86` or `+86`; None for regions without registered rules."""
    return _BY_DIAL_CODE.get(dial_code.removeprefix("+"))


__all__ = ["REGIONS", "RegionRules", "rules_for_dial_code"]
