"""创建类接口的幂等键重放:按 (归属列, 键) 查已有行,窗内则重放。

重放的响应形态(200 + X-Idempotent-Replay)由路由经 core/http.mark_idempotent_replay 统一;
并发同键由各表 UNIQUE(归属列, idempotency_key) 兜底,撞约束后再以本函数回查胜出方。
"""

from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, Mapped

from app.core.timeutil import ensure_utc, now_utc

# 幂等键有效期(实例 / 数据盘):窗口内重放返回既有资源;窗口外同一键按新单处理
IDEMPOTENCY_WINDOW = timedelta(hours=24)


class _HasIdempotencyKey(Protocol):
    idempotency_key: Mapped[str | None]
    created_at: Mapped[datetime]


async def find_replay[RowT: _HasIdempotencyKey](
    session: AsyncSession,
    model: type[RowT],
    *,
    owner_col: InstrumentedAttribute[int] | None,
    owner_id: int | None,
    key: str,
    window: timedelta | None = None,
) -> RowT | None:
    """返回同 (owner, key) 的已有行,没有则 None。

    owner_col 为 None 表示键全局唯一(公告)。window 给定时只认 created_at 在窗内的行:
    窗外的行先释放键位(UNIQUE(归属列, idempotency_key) 不再挡新单)再按无既有行处理。
    """
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
    return existing
