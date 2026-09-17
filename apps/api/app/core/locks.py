"""PostgreSQL advisory locks: single-instance execution of scheduled jobs (settlement / patrols /
reconciler) across replicas."""

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
    PLATFORM_CONFIG_WRITE = 1014


@asynccontextmanager
async def try_advisory_lock(session: AsyncSession, key: LockKey) -> AsyncIterator[bool]:
    """Try to take a PostgreSQL connection-level lock, yield whether it succeeded, unlock on exit.

    While holding the lock the caller must not commit, roll back or switch the underlying
    connection.
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
    """Scheduled-job lock skeleton: try-lock on an independent session, yield whether it was
    taken."""
    async with sm() as lock_session, try_advisory_lock(lock_session, key) as got:
        yield got
