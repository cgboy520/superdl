"""Rate-limit counters: fixed windows stored in PostgreSQL; an independent session commits at once
and never rolls back with the business transaction."""

from datetime import datetime
from typing import Any

from fastapi import status
from sqlalchemy import Row, String, delete, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, get_sessionmaker
from app.core.errors import AppError, ErrorCode


class RateLimitCounter(Base):
    """Fixed-window counter. key carries the dimension prefix, e.g. 'user-login:1.2.3.4:<h>'."""

    __tablename__ = "rate_limit_counters"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    window_start: Mapped[datetime]
    hits: Mapped[int]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


_HIT_SQL = text("""
    INSERT INTO rate_limit_counters AS c (key, window_start, hits, updated_at)
    VALUES (:key, now(), 1, now())
    ON CONFLICT (key) DO UPDATE SET
        window_start = CASE
            WHEN c.window_start <= now() - make_interval(secs => :window) THEN now()
            ELSE c.window_start END,
        hits = CASE
            WHEN c.window_start <= now() - make_interval(secs => :window) THEN 1
            ELSE c.hits + 1 END,
        updated_at = now()
    RETURNING hits,
        GREATEST(
            0, CEIL(EXTRACT(EPOCH FROM (window_start + make_interval(secs => :window) - now())))
        )::int AS retry_after
""")


def _raise_429(retry_after: int) -> None:
    raise AppError(
        ErrorCode.RATE_LIMITED,
        key="common.rateLimited",
        http_status=status.HTTP_429_TOO_MANY_REQUESTS,
        headers={"Retry-After": str(max(1, retry_after))},
    )


async def _fetch_window_row(key: str, window_seconds: float) -> Row[Any] | None:
    """Read the window row only, no row creation, no count."""
    async with get_sessionmaker()() as session:
        return (
            await session.execute(_BLOCKED_SQL, {"key": key[:128], "window": window_seconds})
        ).one_or_none()


async def check_rate_limit(key: str, *, max_attempts: int, window_seconds: float) -> None:
    """Count one hit and judge. Over the limit raises RATE_LIMITED (429 with Retry-After = seconds
    left in the window)."""
    async with get_sessionmaker()() as session:
        row = (await session.execute(_HIT_SQL, {"key": key[:128], "window": window_seconds})).one()
        await session.commit()
    hits, retry_after = row.hits, row.retry_after
    if hits > max_attempts:
        _raise_429(retry_after)


_REFUND_SQL = text("""
    UPDATE rate_limit_counters
    SET hits = GREATEST(hits - 1, 0), updated_at = now()
    WHERE key = :key AND window_start > now() - make_interval(secs => :window)
""")


async def refund_hit(key: str, *, window_seconds: float) -> None:
    """Refund one hit in the window (for count-first buckets after the check passed); no row
    creation, never below 0."""
    async with get_sessionmaker()() as session:
        await session.execute(_REFUND_SQL, {"key": key[:128], "window": window_seconds})
        await session.commit()


async def clear_rate_limit(key: str) -> None:
    """Reset the key's count (independent transaction)."""
    async with get_sessionmaker()() as session:
        await session.execute(delete(RateLimitCounter).where(RateLimitCounter.key == key))
        await session.commit()


_BLOCKED_SQL = text("""
    SELECT hits,
        GREATEST(
            0, CEIL(EXTRACT(EPOCH FROM (window_start + make_interval(secs => :window) - now())))
        )::int AS retry_after
    FROM rate_limit_counters
    WHERE key = :key AND window_start > now() - make_interval(secs => :window)
""")


async def ensure_not_rate_limited(key: str, *, max_attempts: int, window_seconds: float) -> None:
    """429 when hits >= max_attempts in the window; read-only, no count."""
    row = await _fetch_window_row(key, window_seconds)
    if row is not None and row.hits >= max_attempts:
        _raise_429(row.retry_after)


async def read_hits(key: str, *, window_seconds: float) -> int:
    """Current hits in the window (read-only, no count)."""
    row = await _fetch_window_row(key, window_seconds)
    return 0 if row is None else int(row.hits)
