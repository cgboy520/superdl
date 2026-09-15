"""创建类接口的幂等键重放:按 (归属列, 键) 查已有行,窗内重放(200 + X-Idempotent-Replay,
core/http.mark_idempotent_replay);并发同键由 UNIQUE(归属列, idempotency_key) 兜底后回查胜出方;
fingerprint 不符抛 409。
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
    """落库了 request_fingerprint 的创建类表。"""

    request_fingerprint: Mapped[str | None]


def request_fingerprint(*parts: object) -> str:
    """对各参数 repr 以 | 拼接后的 UTF-8 字节取 SHA-256;调用方负责顺序和规范化。"""
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
    """返回同 (owner, key) 的已有行,没有则 None。owner_col=None 键全局唯一;window 给定时窗外行
    先释放键位;fingerprint 给定时不符(含 NULL)抛 409。"""
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
    """插入并 flush 或 commit;返回值 is row 表示新插入。

    IntegrityError 时回滚整个 session,按非空键回查重放行;无匹配行则重抛。
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
