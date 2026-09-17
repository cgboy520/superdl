"""Cursor pagination: ?cursor=&limit=. The cursor is an opaque base64 of the last row's sort key."""

import base64
import binascii
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.errors import AppError, ErrorCode

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None
    total: int | None = None


@dataclass(frozen=True)
class RawPage[T]:
    """Service-layer carrier: items are ORM rows, mapped to Page[Out] by the router."""

    items: list[T]
    next_cursor: str | None = None
    total: int | None = None


def encode_cursor(value: int) -> str:
    return base64.urlsafe_b64encode(str(value).encode()).decode()


def decode_cursor_int(cursor: str | None) -> int | None:
    """Decode the integer sort key; an invalid cursor is VALIDATION_ERROR."""
    if cursor is None:
        return None
    try:
        return int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except (ValueError, binascii.Error) as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="common.badCursor") from exc


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(limit, MAX_LIMIT))


def slice_page[T](
    rows: Sequence[T], lim: int, key: Callable[[T], int]
) -> tuple[list[T], str | None]:
    """With more than lim rows keep the first lim and build the cursor from the last, otherwise
    return all rows and None; lim must be positive."""
    if len(rows) > lim:
        return list(rows[:lim]), encode_cursor(key(rows[lim - 1]))
    return list(rows), None


async def paginate_by_id[RowT](
    session: AsyncSession,
    stmt: Select[tuple[RowT]],
    *,
    id_col: InstrumentedAttribute[int],
    cursor: str | None,
    limit: int | None,
) -> tuple[list[RowT], str | None]:
    """Paginate by id descending; the caller sets id_col.desc() on stmt, this only filters and
    limits."""
    lim = clamp_limit(limit)
    last_id = decode_cursor_int(cursor)
    if last_id is not None:
        stmt = stmt.where(id_col < last_id)
    rows = list((await session.execute(stmt.limit(lim + 1))).scalars())
    return slice_page(rows, lim, key=lambda r: getattr(r, id_col.key))
