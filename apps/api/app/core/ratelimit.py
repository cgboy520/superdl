"""限流计数:固定窗口,计数落 PostgreSQL(多副本共享)。

窗口内命中数超过 max_attempts 即拒绝,窗口到期整体重置。
计数走独立 session 并即时 commit,不随业务事务回滚。
"""

from datetime import datetime

from fastapi import status
from sqlalchemy import String, delete, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, get_sessionmaker
from app.core.errors import AppError, ErrorCode


class RateLimitCounter(Base):
    """固定窗口计数。key 含维度前缀,如 'user-login:1.2.3.4:13800000000'。"""

    __tablename__ = "rate_limit_counters"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    window_start: Mapped[datetime]
    hits: Mapped[int]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


# 单条原子语句:窗口过期即重置为 1,否则自增。
# RETURNING 给出本次命中后的计数与窗口剩余秒数(DB 侧计算,回避应用/库时钟与列时区差异)。
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


async def check_rate_limit(key: str, *, max_attempts: int, window_seconds: float) -> None:
    """记一次命中并判定。超限抛 RATE_LIMITED(429,带 Retry-After 窗口剩余秒数)。"""
    async with get_sessionmaker()() as session:
        row = (await session.execute(_HIT_SQL, {"key": key[:128], "window": window_seconds})).one()
        await session.commit()
    hits, retry_after = row.hits, row.retry_after
    if hits > max_attempts:
        raise AppError(
            ErrorCode.RATE_LIMITED,
            key="common.rateLimited",
            http_status=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(max(1, retry_after))},
        )


async def clear_rate_limit(key: str) -> None:
    """清零该键的计数(登录成功后的失败桶清零;独立事务,不随业务 session 回滚)。"""
    async with get_sessionmaker()() as session:
        await session.execute(delete(RateLimitCounter).where(RateLimitCounter.key == key))
        await session.commit()


# 只读预检:不命中不建行、不计数,专供昂贵校验(如 bcrypt)之前的廉价准入
_BLOCKED_SQL = text("""
    SELECT hits,
        GREATEST(
            0, CEIL(EXTRACT(EPOCH FROM (window_start + make_interval(secs => :window) - now())))
        )::int AS retry_after
    FROM rate_limit_counters
    WHERE key = :key AND window_start > now() - make_interval(secs => :window)
""")


async def ensure_not_rate_limited(key: str, *, max_attempts: int, window_seconds: float) -> None:
    """已达上限的键直接 429(不计数)。与 check_rate_limit 的口径对齐:
    现有 hits ≥ max_attempts 时,下一次计数判定必然超限。"""
    async with get_sessionmaker()() as session:
        row = (
            await session.execute(_BLOCKED_SQL, {"key": key[:128], "window": window_seconds})
        ).one_or_none()
    if row is not None and row.hits >= max_attempts:
        raise AppError(
            ErrorCode.RATE_LIMITED,
            key="common.rateLimited",
            http_status=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(max(1, row.retry_after))},
        )


async def read_hits(key: str, *, window_seconds: float) -> int:
    """窗口内当前命中数(只读,不计数):用于「成功登录前是否有失败记录」类判定。"""
    async with get_sessionmaker()() as session:
        row = (
            await session.execute(_BLOCKED_SQL, {"key": key[:128], "window": window_seconds})
        ).one_or_none()
    return 0 if row is None else int(row.hits)
