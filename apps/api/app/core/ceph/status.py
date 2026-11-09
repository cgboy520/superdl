"""Single-row storage status written by the storage patrol (tenant-mgr): reachability of the Ceph
cluster and the subvolume group's usage. The dispatch gate and the admin cluster page read it,
never Ceph itself."""

from datetime import datetime, timedelta

from sqlalchemy import BigInteger, Text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ceph.base import GroupUsage
from app.core.db import Base
from app.core.timeutil import ensure_utc, now_utc

STORAGE_STATUS_MAX_AGE = timedelta(minutes=5)


class StorageStatus(Base):
    __tablename__ = "storage_status"

    id: Mapped[int] = mapped_column(primary_key=True)
    ok: Mapped[bool]
    error: Mapped[str | None] = mapped_column(Text)
    group_bytes_used: Mapped[int | None] = mapped_column(BigInteger)
    group_bytes_quota: Mapped[int | None] = mapped_column(BigInteger)
    checked_at: Mapped[datetime]


async def get_storage_status(session: AsyncSession) -> StorageStatus | None:
    return await session.get(StorageStatus, 1)


async def save_storage_status(
    session: AsyncSession, *, usage: GroupUsage | None, error: str | None
) -> StorageStatus:
    """Upsert the single row; the caller commits. usage None = the probe failed."""
    row = await session.get(StorageStatus, 1)
    if row is None:
        row = StorageStatus(id=1, ok=False, checked_at=now_utc())
        session.add(row)
    row.ok = usage is not None and error is None
    row.error = error[:1000] if error else None
    if usage is not None:
        row.group_bytes_used = usage.bytes_used
        row.group_bytes_quota = usage.bytes_quota
    row.checked_at = now_utc()
    return row


def storage_ready(row: StorageStatus | None) -> bool:
    """Last probe succeeded and is younger than STORAGE_STATUS_MAX_AGE."""
    return (
        row is not None
        and row.ok
        and now_utc() - ensure_utc(row.checked_at) <= STORAGE_STATUS_MAX_AGE
    )
