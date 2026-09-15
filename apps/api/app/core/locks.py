"""PostgreSQL advisory lock:多副本下保证定时任务(结算/巡检/reconciler)单实例执行。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import IntEnum

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class LockKey(IntEnum):
    HOURLY_SETTLEMENT = 1001
    DAILY_DISK_SETTLEMENT = 1002
    BALANCE_PATROL = 1003
    RECONCILER = 1004
    USAGE_AGGREGATION = 1006
    PAYMENT_RECONCILE = 1007
    PREWARM_PATROL = 1008
    NODE_ENROLL_RECONCILER = 1009
    NODE_SPEC_PATROL = 1010
    FUND_RECONCILE = 1011
    TICKET_STALE_PATROL = 1012
    SUBSCRIPTION_PATROL = 1013


@asynccontextmanager
async def try_advisory_lock(session: AsyncSession, key: LockKey) -> AsyncIterator[bool]:
    """尝试获取 PostgreSQL 连接级锁并 yield 是否成功,退出时解锁。

    持锁期间调用方不得提交、回滚或更换底层连接。
    """
    got = (
        await session.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": int(key)})
    ).scalar_one()
    try:
        yield bool(got)
    finally:
        if got:
            await session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": int(key)})


@asynccontextmanager
async def advisory_lock(sm: async_sessionmaker[AsyncSession], key: LockKey) -> AsyncIterator[bool]:
    """定时任务锁骨架:独立会话拿 try-lock,yield 是否拿到。"""
    async with sm() as lock_session, try_advisory_lock(lock_session, key) as got:
        yield got
