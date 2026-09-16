"""SQL helpers: LIKE/ILIKE metacharacter escaping (single definition, paired with SQLAlchemy's
escape="\\"),
daily order-number sequence, FOR UPDATE row fetch or 404."""

from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, Select, SQLColumnExpression, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.errors import AppError, ErrorCode


def like_escape(q: str) -> str:
    """Escape LIKE metacharacters (\\ % _): callers build f"%{like_escape(q)}%" and pass
    escape="\\"."""
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def next_daily_seq(
    session: AsyncSession, no_col: InstrumentedAttribute[str], prefix: str
) -> int:
    """Next daily order-number sequence (count("{prefix}-%") + 1); concurrent collisions are caught
    by the unique constraint and the caller retries with the next sequence."""
    count = (
        await session.execute(select(func.count()).where(no_col.like(f"{prefix}-%")))
    ).scalar_one()
    return count + 1


async def get_for_update_or_404[T](
    session: AsyncSession, model: type[T], row_id: int, *, key: str
) -> T:
    """FOR UPDATE fetch by primary key; missing → NOT_FOUND (copy key `key`). Shared row-lock entry
    of approval flows / gap replays."""
    row = await session.get(model, row_id, with_for_update=True)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, key=key, http_status=404)
    return row


def total(column: SQLColumnExpression[Decimal]) -> ColumnElement[Decimal]:
    """SUM(col) with an empty set as zero: coalesce(sum(col), 0)."""
    return func.coalesce(func.sum(column), 0)


async def sum_decimal(session: AsyncSession, stmt: Select[tuple[Any]]) -> Decimal:
    """Run a single-column aggregate (usually select(total(col)).where(...)) and return a
    Decimal."""
    return Decimal((await session.execute(stmt)).scalar_one())
