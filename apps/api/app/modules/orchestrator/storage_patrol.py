"""Storage patrol (tenant-mgr, 60 s, advisory lock): probe the Ceph cluster through the subvolume
group (usage + quota → storage_status, the dispatch gate's source), then refresh the used bytes
of the volumes checked longest ago, a bounded batch per round."""

from sqlalchemy import nulls_first, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.ceph import CephAdmin, CephError, GroupUsage, load_ceph
from app.core.ceph.status import save_storage_status
from app.core.locks import LockKey, advisory_lock
from app.core.logging import get_logger
from app.core.metrics import CEPH_GROUP_BYTES, CEPH_REACHABLE
from app.core.timeutil import now_utc
from app.modules.orchestrator import statemachine as sm_def
from app.modules.orchestrator.models import DataDisk, Instance
from app.modules.orchestrator.storage import data_volume_name, system_volume_name

logger = get_logger(__name__)

USAGE_BATCH = 100


async def storage_patrol(sm: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    counts = {"probed": 0, "usage_refreshed": 0, "usage_failed": 0}
    async with advisory_lock(sm, LockKey.STORAGE_PATROL) as got:
        if not got:
            return counts
        async with sm() as session:
            _settings, admin = await load_ceph(session)
        usage: GroupUsage | None = None
        error: str | None = None
        try:
            usage = await admin.group_usage()
        except CephError as exc:
            error = str(exc)
            logger.warning("storage_probe_failed", error=error)
        async with sm() as session:
            await save_storage_status(session, usage=usage, error=error)
            await session.commit()
        _export(usage)
        counts["probed"] = 1
        if usage is not None:
            await _refresh_disk_usage(sm, admin, counts)
            await _refresh_system_usage(sm, admin, counts)
    return counts


def _export(usage: GroupUsage | None) -> None:
    CEPH_REACHABLE.set(0 if usage is None else 1)
    if usage is not None:
        CEPH_GROUP_BYTES.labels(kind="used").set(usage.bytes_used)
        if usage.bytes_quota is not None:
            CEPH_GROUP_BYTES.labels(kind="quota").set(usage.bytes_quota)


async def _refresh_disk_usage(
    sm: async_sessionmaker[AsyncSession], admin: CephAdmin, counts: dict[str, int]
) -> None:
    async with sm() as session:
        disks = list(
            (
                await session.execute(
                    select(DataDisk)
                    .where(DataDisk.ceph_path.is_not(None), DataDisk.status != "deleted")
                    .order_by(nulls_first(DataDisk.usage_checked_at), DataDisk.id)
                    .limit(USAGE_BATCH)
                )
            ).scalars()
        )
        for disk in disks:
            try:
                used = await admin.subvolume_usage(data_volume_name(disk))
            except CephError as exc:
                counts["usage_failed"] += 1
                logger.warning("disk_usage_failed", disk_id=disk.id, error=str(exc))
                continue
            disk.bytes_used = used.bytes_used if used is not None else None
            disk.usage_checked_at = now_utc()
            counts["usage_refreshed"] += 1
        await session.commit()


async def _refresh_system_usage(
    sm: async_sessionmaker[AsyncSession], admin: CephAdmin, counts: dict[str, int]
) -> None:
    async with sm() as session:
        instances = list(
            (
                await session.execute(
                    select(Instance)
                    .where(
                        Instance.system_disk_path.is_not(None),
                        Instance.status != sm_def.RELEASED,
                    )
                    .order_by(nulls_first(Instance.disk_usage_checked_at), Instance.id)
                    .limit(USAGE_BATCH)
                )
            ).scalars()
        )
        for instance in instances:
            try:
                used = await admin.subvolume_usage(system_volume_name(instance))
            except CephError as exc:
                counts["usage_failed"] += 1
                logger.warning("system_disk_usage_failed", instance_id=instance.id, error=str(exc))
                continue
            instance.system_disk_bytes_used = used.bytes_used if used is not None else None
            instance.disk_usage_checked_at = now_utc()
            counts["usage_refreshed"] += 1
        await session.commit()
