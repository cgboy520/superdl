"""Idempotency-key replay for creation endpoints: look up the existing row by (owner column, key);
replay within the window (200 + X-Idempotent-Replay, core/http.mark_idempotent_replay); concurrent
same-key inserts are caught by UNIQUE(owner column, idempotency_key) and the winner is re-read;
a fingerprint mismatch raises 409.
"""

import hashlib
from datetime import datetime, timedelta
from typing import Any, Protocol, cast, overload

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, Mapped

from app.core.errors import conflict
from app.core.timeutil import ensure_utc, now_utc

IDEMPOTENCY_WINDOW = timedelta(hours=24)


class _HasIdempotencyKey(Protocol):
    idempotency_key: Mapped[str | None]
    created_at: Mapped[datetime]


class _HasRequestFingerprint(_HasIdempotencyKey, Protocol):
    """Creation tables that store request_fingerprint."""

    request_fingerprint: Mapped[str | None]


def request_fingerprint(*parts: object) -> str:
    """SHA-256 over the UTF-8 bytes of the parameters' repr joined with |; the caller owns ordering
    and normalisation."""
    return hashlib.sha256("|".join(repr(p) for p in parts).encode()).hexdigest()


@overload
async def find_replay[RowT: _HasIdempotencyKey](
    session: AsyncSession,
    model: type[RowT],
    *,
    owner_col: InstrumentedAttribute[int] | None,
    owner_id: int | None,
    key: str,
    window: timedelta | None = None,
    fingerprint: None = None,
) -> RowT | None: ...


@overload
async def find_replay[RowT: _HasRequestFingerprint](
    session: AsyncSession,
    model: type[RowT],
    *,
    owner_col: InstrumentedAttribute[int] | None,
    owner_id: int | None,
    key: str,
    window: timedelta | None = None,
    fingerprint: str,
) -> RowT | None: ...


async def find_replay(
    session: AsyncSession,
    model: type[Any],
    *,
    owner_col: InstrumentedAttribute[int] | None,
    owner_id: int | None,
    key: str,
    window: timedelta | None = None,
    fingerprint: str | None = None,
) -> Any | None:
    """The existing row for the same (owner, key), else None. owner_col=None means a globally unique
    key; with a window, rows outside it release the key first;
    with a fingerprint, a mismatch (including NULL) raises 409."""
    stmt = select(model).where(model.idempotency_key == key)
    if owner_col is not None:
        stmt = stmt.where(owner_col == owner_id)
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is None:
        return None
    if window is not None and now_utc() - ensure_utc(existing.created_at) >= window:
        existing.idempotency_key = None
        await session.flush()
        return None
    if fingerprint is not None and existing.request_fingerprint != fingerprint:
        raise conflict(key="common.idempotencyKeyMismatch")
    return existing


async def insert_idempotent[RowT: _HasIdempotencyKey](
    session: AsyncSession,
    row: RowT,
    *,
    model: type[RowT],
    owner_col: InstrumentedAttribute[int] | None,
    owner_id: int | None,
    key: str | None,
    window: timedelta | None = None,
    fingerprint: str | None = None,
    commit: bool = False,
) -> RowT:
    """Insert and flush or commit; a return value `is row` means a fresh insert.

    On IntegrityError the whole session is rolled back and the replay row re-read by the non-empty
    key; re-raised when no row matches.
    """
    session.add(row)
    try:
        if commit:
            await session.commit()
        else:
            await session.flush()
    except IntegrityError:
        await session.rollback()
        if key is not None:
            if fingerprint is not None:
                winner = await find_replay(
                    session,
                    cast(type[Any], model),
                    owner_col=owner_col,
                    owner_id=owner_id,
                    key=key,
                    window=window,
                    fingerprint=fingerprint,
                )
            else:
                winner = await find_replay(
                    session, model, owner_col=owner_col, owner_id=owner_id, key=key, window=window
                )
            if winner is not None:
                return cast(RowT, winner)
        raise
    return row
