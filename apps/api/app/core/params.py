"""Query parameters and idempotency request headers shared by the routers."""

from typing import Annotated

from fastapi import Depends, Header, Query
from pydantic import WithJsonSchema

from app.core.compliance import current_profile
from app.core.locale import Locale
from app.core.pagination import MAX_LIMIT
from app.core.timeutil import billing_offset_minutes

_TZ_OFFSET_SCHEMA = {"type": "integer", "minimum": -720, "maximum": 840}


def _tz_offset(
    tz_offset_minutes: Annotated[
        int | None, Query(ge=-720, le=840), WithJsonSchema(_TZ_OFFSET_SCHEMA)
    ] = None,
) -> int:
    """Client UTC offset for display aggregation (UTC-12 … UTC+14); omitted → the billing zone's
    current offset. Published as an optional integer, never as nullable."""
    return tz_offset_minutes if tz_offset_minutes is not None else billing_offset_minutes()


TzOffset = Depends(_tz_offset)


def _export_lang(lang: Locale | None = Query(default=None)) -> str:
    """CSV language; omitted → the compliance profile's default locale."""
    return lang or current_profile().default_locale


ExportLang = Depends(_export_lang)

Cursor = Query(default=None)
Limit = Query(default=None, le=MAX_LIMIT)

IDEMPOTENCY_KEY_MAX_LENGTH = 64
IdempotencyKey = Annotated[
    str | None, Header(alias="Idempotency-Key", max_length=IDEMPOTENCY_KEY_MAX_LENGTH)
]
