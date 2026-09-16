"""Data-disk service: create / expand / delete / mount management, independent of the instance
lifecycle."""

from uuid import uuid4

from sqlalchemy import func, select, update
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
from app.modules.orchestrator.models import DataDisk, Instance
from app.modules.orchestrator.queries import DISK_BILLABLE_STATUSES

logger = get_logger(__name__)


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
) -> tuple[DataDisk, bool]:
    """Create the data disk and commit the provisioning task; returns (disk, created), created=False
    on an idempotent replay."""
    fingerprint = request_fingerprint(user_id, name, size_gb)
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
    await nodes_service.require_storage_classes(session, with_data_disk=True)
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
    disk_uuid = uuid4().hex
    disk = DataDisk(
        uuid=disk_uuid,
        user_id=user_id,
        name=name,
        size_gb=size_gb,
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


async def expand_disk(session: AsyncSession, user_id: int, uuid: str, new_size_gb: int) -> DataDisk:
    disk = (
        await session.execute(
            select(DataDisk)
            .where(DataDisk.uuid == uuid, DataDisk.user_id == user_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if disk is None or disk.status == "deleted":
        raise not_found(key="orchestrator.diskNotFound")
    if disk.status != "active":
        raise AppError(ErrorCode.VALIDATION_ERROR, key="disks.expandNeedsActive")
    if new_size_gb <= disk.size_gb:
        raise AppError(ErrorCode.DISK_SHRINK_FORBIDDEN, key="disks.shrinkForbidden")
    policies = await get_runtime_config(session)
    max_gb = policies.disk_max_gb
    if new_size_gb > max_gb:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="disks.sizeMax", params={"max": max_gb})
    _live, used_gb = await _disk_usage(session, user_id)
    _check_capacity_quota(used_gb - disk.size_gb, new_size_gb, policies.max_disk_gb_per_user)
    await _settle_pending_days(session, disk)
    delta_daily = disk_daily_charge(disk.price_gb_month, new_size_gb) - disk_daily_charge(
        disk.price_gb_month, disk.size_gb
    )
    await billing_service.assert_can_afford(session, user_id, additional_daily_disk=delta_daily)
    disk.size_gb = new_size_gb
    disk.provisioned = False
    enqueue(session, "disk.provision", {"disk_id": disk.id})
    await session.commit()
    return disk


async def delete_disk(session: AsyncSession, user_id: int, uuid: str) -> DataDisk:
    """Delete: forbidden while mounted; enters deleting, the outbox deletes the PVC and then marks
    deleted."""
    disk = (
        await session.execute(
            select(DataDisk)
            .where(DataDisk.uuid == uuid, DataDisk.user_id == user_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if disk is None or disk.status == "deleted":
        raise not_found(key="orchestrator.diskNotFound")
    if disk.mounted_instance_id is not None:
        inst = await session.get(Instance, disk.mounted_instance_id)
        if inst is not None and inst.status in ("stopped", "frozen", "failed"):
            disk.mounted_instance_id = None
        else:
            raise AppError(ErrorCode.DISK_IN_USE, key="disks.inUseDelete")
    if disk.status == "deleting":
        return disk
    await _settle_pending_days(session, disk)
    disk.status = "deleting"
    await session.execute(
        update(Instance).where(Instance.data_disk_id == disk.id).values(data_disk_id=None)
    )
    enqueue(session, "disk.deprovision", {"disk_id": disk.id})
    await session.commit()
    return disk


async def attach_for_instance(session: AsyncSession, user_id: int, disk_id: int, instance_id: int):
    """Mount check + occupation at instance creation (FOR UPDATE on the disk row). Called in the
    same
    transaction, no commit."""
    disk = await session.get(DataDisk, disk_id, with_for_update=True)
    if disk is None or disk.user_id != user_id or disk.status == "deleted":
        raise not_found(key="orchestrator.diskNotFound")
    if disk.status != "active":
        raise AppError(ErrorCode.VALIDATION_ERROR, key="disks.notMountable")
    if not disk.provisioned:
        raise conflict(key="disks.notProvisioned")
    if disk.mounted_instance_id is not None and disk.mounted_instance_id != instance_id:
        raise AppError(ErrorCode.DISK_IN_USE, key="disks.mountedElsewhere")
    disk.mounted_instance_id = instance_id
    return disk


async def detach_for_instance(session: AsyncSession, instance_id: int) -> None:
    """Detach when the instance reaches a terminal state (released/failed). Same transaction."""
    disk = (
        await session.execute(select(DataDisk).where(DataDisk.mounted_instance_id == instance_id))
    ).scalar_one_or_none()
    if disk is not None:
        disk.mounted_instance_id = None


async def lock_disk_for_attach(session: AsyncSession, user_id: int, disk_id: int) -> DataDisk:
    """Ownership check + FOR UPDATE row lock before mounting at instance creation; lock order disk →
    wallet."""
    disk = await session.get(DataDisk, disk_id, with_for_update=True)
    if disk is None or disk.user_id != user_id or disk.status == "deleted":
        raise not_found(key="orchestrator.diskNotFound")
    return disk
