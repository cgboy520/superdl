"""游标分页:?cursor=&limit=。cursor 为不透明 base64(最后一行的排序键)。"""

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
    """service 层内部载体:items 为 ORM 行,由 router 映射成 Page[Out]。"""

    items: list[T]
    next_cursor: str | None = None
    total: int | None = None


def encode_cursor(value: int) -> str:
    return base64.urlsafe_b64encode(str(value).encode()).decode()


def decode_cursor_int(cursor: str | None) -> int | None:
    """解出整型排序键;非法 cursor 报 VALIDATION_ERROR。"""
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
    """行数超过 lim 时截取前 lim 行并生成末行游标,否则返回全部行和 None;lim 须为正。"""
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
    """按 id 降序分页;调用方须在 stmt 设置 id_col.desc() 排序,本函数只过滤和限行。"""
    lim = clamp_limit(limit)
    last_id = decode_cursor_int(cursor)
    if last_id is not None:
        stmt = stmt.where(id_col < last_id)
    rows = list((await session.execute(stmt.limit(lim + 1))).scalars())
    return slice_page(rows, lim, key=lambda r: getattr(r, id_col.key))
