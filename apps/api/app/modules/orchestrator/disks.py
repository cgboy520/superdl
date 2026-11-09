"""Data-disk service: create / update / delete, independent of the instance lifecycle. Every
disk of a user is mounted on all of that user's instances: each instance snapshots the user's
mountable disks when it is created, started or restarted (instances.data_disk_ids)."""

import re
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode, conflict, not_found
from app.core.idempotency import (
    IDEMPOTENCY_WINDOW,
    find_replay,
    insert_idempotent,
    request_fingerprint,
)
from app.core.logging import get_logger
from app.core.money import as_price, disk_daily_charge
from app.core.outbox import enqueue
from app.core.platform_config import get_runtime_config
from app.modules.account import service as account_service
from app.modules.billing import service as billing_service
from app.modules.nodes import service as nodes_service
from app.modules.orchestrator import statemachine as sm_def
from app.modules.orchestrator.models import DataDisk, Instance
from app.modules.orchestrator.queries import DISK_BILLABLE_STATUSES

logger = get_logger(__name__)

DEFAULT_MOUNT_PATH = "/data"
# A mount point of its own under /data*, /workspace* or /mnt/<name>; /mnt/org/ belongs to the
# organization's storage, /root to the system disk, every other path to the image.
_MOUNT_PATH_RE = re.compile(
    r"/(?:data[A-Za-z0-9._-]{0,32}|workspace[A-Za-z0-9._-]{0,32}"
    r"|mnt/(?!org$)[A-Za-z0-9][A-Za-z0-9._-]{0,63})"
)
MOUNTED_STATUSES = ("active", "grace")
# Instance states whose Pod exists or is being created: their snapshot is (about to be) mounted.
_POD_STATES = (
    sm_def.CREATING,
    sm_def.STARTING,
    sm_def.RUNNING,
    sm_def.STOPPING,
    sm_def.RELEASING,
)


def valid_mount_path(path: str) -> bool:
    return _MOUNT_PATH_RE.fullmatch(path) is not None


async def mountable_disk_ids(session: AsyncSession, user_id: int) -> list[int]:
    """The disks an instance of the user mounts from its next Pod on: active (read-write) and
    grace (read-only); frozen and deleted disks are left out."""
    return list(
        (
            await session.execute(
                select(DataDisk.id)
                .where(DataDisk.user_id == user_id, DataDisk.status.in_(MOUNTED_STATUSES))
                .order_by(DataDisk.id)
            )
        ).scalars()
    )


async def disk_users(
    session: AsyncSession, user_id: int, disk_ids: list[int]
) -> dict[int, list[Instance]]:
    """Instances whose Pod mounts each disk (states with a Pod); one query over the user's
    instances, filtered by snapshot."""
    if not disk_ids:
        return {}
    rows = (
        await session.execute(
            select(Instance)
            .where(Instance.user_id == user_id, Instance.status.in_(_POD_STATES))
            .order_by(Instance.id)
        )
    ).scalars()
    wanted = set(disk_ids)
    out: dict[int, list[Instance]] = {}
    for inst in rows:
        for disk_id in inst.data_disk_ids or ():
            if disk_id in wanted:
                out.setdefault(disk_id, []).append(inst)
    return out


async def _check_mount_path_free(
    session: AsyncSession, user_id: int, path: str, *, exclude_id: int | None = None
) -> None:
    if not valid_mount_path(path):
        raise AppError(ErrorCode.VALIDATION_ERROR, key="disks.mountPathInvalid")
    stmt = select(DataDisk.id).where(
        DataDisk.user_id == user_id, DataDisk.status != "deleted", DataDisk.mount_path == path
    )
    if exclude_id is not None:
        stmt = stmt.where(DataDisk.id != exclude_id)
    if (await session.execute(stmt.limit(1))).first() is not None:
        raise conflict(key="disks.mountPathTaken", params={"path": path})


async def _disk_usage(session: AsyncSession, user_id: int) -> tuple[int, int]:
    """(non-deleted disk count, total non-deleted capacity in GB)."""
    row = (
        await session.execute(
            select(func.count(), func.coalesce(func.sum(DataDisk.size_gb), 0)).where(
                DataDisk.user_id == user_id, DataDisk.status != "deleted"
            )
        )
    ).one()
    return int(row[0]), int(row[1])


def _check_capacity_quota(used_gb: int, delta_gb: int, max_gb: int) -> None:
    """Total capacity follows the policy max_disk_gb_per_user; exceeding is rejected."""
    if used_gb + delta_gb > max_gb:
        raise AppError(
            ErrorCode.VALIDATION_ERROR, key="disks.capacityQuota", params={"max": max_gb}
        )


async def create_disk(
    session: AsyncSession,
    user_id: int,
    name: str,
    size_gb: int,
    idempotency_key: str | None = None,
    *,
    mount_path: str = DEFAULT_MOUNT_PATH,
) -> tuple[DataDisk, bool]:
    """Create the data disk and commit the provisioning task; returns (disk, created), created=False
    on an idempotent replay. The disk joins every instance of the user from its next start."""
    fingerprint = request_fingerprint(user_id, name, size_gb, mount_path)
    if idempotency_key:
        existing = await find_replay(
            session,
            DataDisk,
            owner_col=DataDisk.user_id,
            owner_id=user_id,
            key=idempotency_key,
            window=IDEMPOTENCY_WINDOW,
            fingerprint=fingerprint,
        )
        if existing is not None:
            return existing, False
    await nodes_service.require_storage_ready(session)
    policies = await get_runtime_config(session)
    if not policies.disk_min_gb <= size_gb <= policies.disk_max_gb:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="disks.sizeRange",
            params={"min": policies.disk_min_gb, "max": policies.disk_max_gb},
        )
    price = as_price(policies.disk_price_gb_month)
    daily = disk_daily_charge(price, size_gb)
    await billing_service.assert_can_afford(session, user_id, additional_daily_disk=daily)
    limits = await account_service.get_user_limits(session, user_id)
    max_disks = limits.max_disks
    live, used_gb = await _disk_usage(session, user_id)
    if live >= max_disks:
        raise AppError(
            ErrorCode.VALIDATION_ERROR, key="disks.countQuota", params={"max": max_disks}
        )
    _check_capacity_quota(used_gb, size_gb, policies.max_disk_gb_per_user)
    await _check_mount_path_free(session, user_id, mount_path)
    disk_uuid = uuid4().hex
    disk = DataDisk(
        uuid=disk_uuid,
        user_id=user_id,
        name=name,
        size_gb=size_gb,
        mount_path=mount_path,
        price_gb_month=price,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
    )
    result = await insert_idempotent(
        session,
        disk,
        model=DataDisk,
        owner_col=DataDisk.user_id,
        owner_id=user_id,
        key=idempotency_key,
        fingerprint=fingerprint,
    )
    if result is not disk:
        return result, False
    enqueue(session, "disk.provision", {"disk_id": disk.id})
    await session.commit()
    await session.refresh(disk)
    logger.info("disk_created", disk_id=disk.id, user_id=user_id, size_gb=size_gb)
    return disk, True


async def get_disk(session: AsyncSession, user_id: int, uuid: str) -> DataDisk:
    disk = (
        await session.execute(
            select(DataDisk).where(DataDisk.uuid == uuid, DataDisk.user_id == user_id)
        )
    ).scalar_one_or_none()
    if disk is None or disk.status == "deleted":
        raise not_found(key="orchestrator.diskNotFound")
    return disk


async def list_disks(session: AsyncSession, user_id: int) -> list[DataDisk]:
    return list(
        (
            await session.execute(
                select(DataDisk)
                .where(DataDisk.user_id == user_id, DataDisk.status != "deleted")
                .order_by(DataDisk.id.desc())
            )
        ).scalars()
    )


def disk_billing_input(disk: DataDisk) -> billing_service.DiskBillingInput:
    """Convert the DataDisk row into settlement input."""
    return billing_service.DiskBillingInput(
        id=disk.id,
        user_id=disk.user_id,
        price_gb_month=disk.price_gb_month,
        size_gb=disk.size_gb,
        created_at=disk.created_at,
    )


async def _settle_pending_days(session: AsyncSession, disk: DataDisk) -> None:
    """Settle the unbilled calendar days at the pre-change size (same transaction). Disks not in a
    billable state are not back-billed."""
    if disk.status not in DISK_BILLABLE_STATUSES:
        return
    await billing_service.settle_disk_pending_days(session, disk_billing_input(disk))


async def _locked_disk(session: AsyncSession, user_id: int, uuid: str) -> DataDisk:
    disk = (
        await session.execute(
            select(DataDisk)
            .where(DataDisk.uuid == uuid, DataDisk.user_id == user_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if disk is None or disk.status == "deleted":
        raise not_found(key="orchestrator.diskNotFound")
    return disk


async def _expand(session: AsyncSession, disk: DataDisk, new_size_gb: int) -> None:
    """Grow only; settles the days not yet billed at the old size, then re-applies the quota."""
    if disk.status != "active":
        raise AppError(ErrorCode.VALIDATION_ERROR, key="disks.expandNeedsActive")
    if new_size_gb <= disk.size_gb:
        raise AppError(ErrorCode.DISK_SHRINK_FORBIDDEN, key="disks.shrinkForbidden")
    policies = await get_runtime_config(session)
    max_gb = policies.disk_max_gb
    if new_size_gb > max_gb:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="disks.sizeMax", params={"max": max_gb})
    _live, used_gb = await _disk_usage(session, disk.user_id)
    _check_capacity_quota(used_gb - disk.size_gb, new_size_gb, policies.max_disk_gb_per_user)
    await _settle_pending_days(session, disk)
    delta_daily = disk_daily_charge(disk.price_gb_month, new_size_gb) - disk_daily_charge(
        disk.price_gb_month, disk.size_gb
    )
    await billing_service.assert_can_afford(
        session, disk.user_id, additional_daily_disk=delta_daily
    )
    disk.size_gb = new_size_gb
    disk.provisioned = False
    enqueue(session, "disk.provision", {"disk_id": disk.id})


async def update_disk(
    session: AsyncSession,
    user_id: int,
    uuid: str,
    *,
    size_gb: int | None = None,
    mount_path: str | None = None,
) -> DataDisk:
    """Expand (never shrink) and / or move the mount point; a new mount point applies to each
    instance from its next start."""
    disk = await _locked_disk(session, user_id, uuid)
    if size_gb is not None and size_gb != disk.size_gb:
        await _expand(session, disk, size_gb)
    if mount_path is not None and mount_path != disk.mount_path:
        await _check_mount_path_free(session, user_id, mount_path, exclude_id=disk.id)
        disk.mount_path = mount_path
    await session.commit()
    return disk


async def delete_disk(session: AsyncSession, user_id: int, uuid: str) -> DataDisk:
    """Delete: forbidden while a Pod of the user mounts it (stop those instances first); enters
    deleting, the outbox unbinds the volume and removes the subvolume, then marks deleted."""
    disk = await _locked_disk(session, user_id, uuid)
    if disk.status == "deleting":
        return disk
    users = (await disk_users(session, user_id, [disk.id])).get(disk.id, [])
    if users:
        raise AppError(
            ErrorCode.DISK_IN_USE,
            key="disks.inUseDelete",
            params={"names": ", ".join(i.name for i in users[:3])},
        )
    await _settle_pending_days(session, disk)
    disk.status = "deleting"
    enqueue(session, "disk.deprovision", {"disk_id": disk.id})
    await session.commit()
    return disk
