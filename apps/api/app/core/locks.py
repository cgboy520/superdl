"""PostgreSQL advisory lock:多副本下保证定时任务(结算/巡检/reconciler)单实例执行。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import IntEnum

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class LockKey(IntEnum):
    HOURLY_SETTLEMENT = 1001
    DAILY_DISK_SETTLEMENT = 1002
    BALANCE_PATROL = 1003
    RECONCILER = 1004
    OUTBOX_REAPER = 1005
    USAGE_AGGREGATION = 1006
    PAYMENT_RECONCILE = 1007
    PREWARM_PATROL = 1008


@asynccontextmanager
async def try_advisory_lock(session: AsyncSession, key: LockKey) -> AsyncIterator[bool]:
    """会话级 try-lock。yield 是否拿到;未拿到调用方应直接跳过本轮。

    锁随事务外的 session 存续,退出时显式释放。
    """
    got = (
        await session.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": int(key)})
    ).scalar_one()
    try:
        yield bool(got)
    finally:
        if got:
            await session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": int(key)})
