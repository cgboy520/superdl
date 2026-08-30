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
    # 可选精确计数(默认不算:全表 COUNT 在大表上不值得;仅调用方确有需要时才附,
    # 如管理端租户抽屉要区分「正好 100 条」与「被 100 条上限截断」)
    total: int | None = None


@dataclass(frozen=True)
class RawPage[T]:
    """service 层内部载体:items 为 ORM 行(无 pydantic schema),由 router 映射成 Page[Out]。"""

    items: list[T]
    next_cursor: str | None = None
    total: int | None = None


def encode_cursor(value: int | str) -> str:
    return base64.urlsafe_b64encode(str(value).encode()).decode()


def decode_cursor_int(cursor: str | None) -> int | None:
    """解出整型排序键(通常是自增 id)。非法 cursor 报 VALIDATION_ERROR。"""
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
    """lim+1 取行切页(降序游标的标准收尾):满页回 (前 lim 行, 以第 lim 行 key 编码的
    next_cursor),否则 (全部行, None)。调用方负责按 lim+1 取行并按 key 降序排列。"""
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
    """id 降序游标分页的标准执行骨架(单一定义点,防各 service 逐字复制)。

    调用方 stmt 只需声明过滤条件与 .order_by(id_col.desc());本函数负责:
    clamp_limit → 解游标 → 追加 id < last → limit(lim+1) → 取行 → slice_page。
    返回 (前 lim 行, next_cursor)。"""
    lim = clamp_limit(limit)
    last_id = decode_cursor_int(cursor)
    if last_id is not None:
        stmt = stmt.where(id_col < last_id)
    rows = list((await session.execute(stmt.limit(lim + 1))).scalars())
    return slice_page(rows, lim, key=lambda r: getattr(r, id_col.key))
