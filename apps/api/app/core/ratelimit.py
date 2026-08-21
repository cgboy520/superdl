"""限流计数:固定窗口,计数落 PostgreSQL(多副本共享)。

进程内计数在多副本下等于把阈值乘以副本数(已交付清单是 2 副本 → 「5 次锁定」
现网实为 10 次,短信轰炸上限同倍放大),因此计数必须出进程。

计数走独立 session 并即时 commit:业务事务失败回滚不能把「这次尝试」抹掉,
否则密码/验证码爆破可以靠触发业务错误来无限重试。

语义:固定窗口(窗口内命中数超过 max_attempts 即拒绝,窗口到期整体重置)。
比原先的滑动窗口宽松一个边界(最坏情形两窗口交界处放行 2×max_attempts),
换来单条 SQL 的原子性与跨副本一致。
"""

from datetime import datetime

from fastapi import status
from sqlalchemy import String, func, text
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


# 单条原子语句:窗口过期即重置为 1,否则自增。RETURNING 给出本次命中后的计数。
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
    RETURNING hits
""")


async def check_rate_limit(key: str, *, max_attempts: int, window_seconds: float) -> None:
    """记一次命中并判定。超限抛 RATE_LIMITED(429)。"""
    async with get_sessionmaker()() as session:
        hits = (
            await session.execute(_HIT_SQL, {"key": key[:128], "window": window_seconds})
        ).scalar_one()
        await session.commit()
    if hits > max_attempts:
        raise AppError(
            ErrorCode.RATE_LIMITED,
            key="common.rateLimited",
            http_status=status.HTTP_429_TOO_MANY_REQUESTS,
        )
