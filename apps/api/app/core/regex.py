"""Re-export of the mainland-China phone shapes from `app.core.regions.cn` for current consumers."""

from app.core.regions.cn import (
    NATIONAL_PHONE_RE as PHONE_RE,
    NATIONAL_PHONE_RE_LOOSE as PHONE_RE_LOOSE,
)

__all__ = ["PHONE_RE", "PHONE_RE_LOOSE"]
