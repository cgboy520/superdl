"""数据盘服务:创建/扩容/删除/挂载管理。独立于实例生命周期(留存抓手)。"""

from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode, not_found
from app.core.logging import get_logger
from app.core.money import as_price, disk_daily_charge
from app.core.outbox import enqueue
from app.core.timeutil import now_utc
from app.modules.billing import service as billing_service
from app.modules.orchestrator.models import DataDisk

logger = get_logger(__name__)

BILLABLE_STATUSES = ("active", "grace")  # frozen 不再计费


async def create_disk(session: AsyncSession, user_id: int, name: str, size_gb: int) -> DataDisk:
    settings = get_settings()
    if not settings.disk_min_gb <= size_gb <= settings.disk_max_gb:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            f"容量须在 {settings.disk_min_gb}~{settings.disk_max_gb} GB 之间",
        )
    price = as_price(Decimal(settings.disk_price_gb_month))
    daily = disk_daily_charge(price, size_gb)
    await billing_service.require_balance_at_least(
        session, user_id, daily, hint=f"新建数据盘需要至少 1 日费用 ¥{daily}"
    )
    disk_uuid = uuid4().hex
    disk = DataDisk(
        uuid=disk_uuid,
        user_id=user_id,
        name=name,
        size_gb=size_gb,
        juicefs_subpath=f"disk-{disk_uuid}",
        price_gb_month=price,
    )
    session.add(disk)
    await session.commit()
    await session.refresh(disk)
    logger.info("disk_created", disk_id=disk.id, user_id=user_id, size_gb=size_gb)
    return disk


async def get_disk(session: AsyncSession, user_id: int, uuid: str) -> DataDisk:
    disk = (
        await session.execute(
            select(DataDisk).where(DataDisk.uuid == uuid, DataDisk.user_id == user_id)
        )
    ).scalar_one_or_none()
    if disk is None or disk.status == "deleted":
        raise not_found("数据盘不存在")
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


async def expand_disk(session: AsyncSession, user_id: int, uuid: str, new_size_gb: int) -> DataDisk:
    disk = await get_disk(session, user_id, uuid)
    if disk.status != "active":
        raise AppError(ErrorCode.VALIDATION_ERROR, "仅正常状态的数据盘可以扩容")
    if new_size_gb <= disk.size_gb:
        raise AppError(ErrorCode.DISK_SHRINK_FORBIDDEN, "数据盘只支持扩容,不支持缩容")
    if new_size_gb > get_settings().disk_max_gb:
        raise AppError(ErrorCode.VALIDATION_ERROR, f"容量上限 {get_settings().disk_max_gb} GB")
    disk.size_gb = new_size_gb
    await session.commit()
    return disk


async def delete_disk(session: AsyncSession, user_id: int, uuid: str) -> DataDisk:
    """删除(前端多级防护后调用)。挂载中禁止;进入 deleting,由 outbox 擦除后置 deleted。"""
    disk = await get_disk(session, user_id, uuid)
    if disk.mounted_instance_id is not None:
        raise AppError(ErrorCode.DISK_IN_USE, "数据盘挂载中,请先释放对应实例")
    if disk.status == "deleting":
        return disk
    disk.status = "deleting"
    enqueue(session, "disk.wipe", {"disk_id": disk.id})
    await session.commit()
    return disk


async def attach_for_instance(session: AsyncSession, user_id: int, disk_id: int, instance_id: int):
    """实例创建时挂载校验 + 占用。同事务调用,不 commit。"""
    disk = await session.get(DataDisk, disk_id)
    if disk is None or disk.user_id != user_id or disk.status == "deleted":
        raise not_found("数据盘不存在")
    if disk.status != "active":
        raise AppError(ErrorCode.VALIDATION_ERROR, "数据盘当前状态不可挂载")
    if disk.mounted_instance_id is not None and disk.mounted_instance_id != instance_id:
        raise AppError(ErrorCode.DISK_IN_USE, "数据盘已挂载到其他实例")
    disk.mounted_instance_id = instance_id
    return disk


async def detach_for_instance(session: AsyncSession, instance_id: int) -> None:
    """实例终态(released/failed)时解除挂载。同事务调用。"""
    disk = (
        await session.execute(select(DataDisk).where(DataDisk.mounted_instance_id == instance_id))
    ).scalar_one_or_none()
    if disk is not None:
        disk.mounted_instance_id = None


async def list_billable_disks(session: AsyncSession) -> list[DataDisk]:
    return list(
        (
            await session.execute(select(DataDisk).where(DataDisk.status.in_(BILLABLE_STATUSES)))
        ).scalars()
    )


async def arrears_transition_disks(session: AsyncSession, user_id: int, in_arrears: bool) -> int:
    """欠费巡检钩子:active↔grace→frozen→deleting 链路。返回变更数。"""
    settings = get_settings()
    now = now_utc()
    changed = 0
    disks = list(
        (
            await session.execute(
                select(DataDisk).where(
                    DataDisk.user_id == user_id,
                    DataDisk.status.in_(("active", "grace", "frozen")),
                )
            )
        ).scalars()
    )
    for disk in disks:
        if not in_arrears:
            if disk.status in ("grace", "frozen"):
                disk.status = "active"
                disk.grace_started_at = None
                disk.frozen_started_at = None
                changed += 1
            continue
        if disk.status == "active":
            disk.status = "grace"
            disk.grace_started_at = now
            changed += 1
        elif disk.status == "grace" and disk.grace_started_at is not None:
            from datetime import timedelta

            if now - disk.grace_started_at > timedelta(days=settings.disk_grace_days):
                disk.status = "frozen"
                disk.frozen_started_at = now
                changed += 1
        elif disk.status == "frozen" and disk.frozen_started_at is not None:
            from datetime import timedelta

            if now - disk.frozen_started_at > timedelta(days=settings.disk_frozen_days):
                disk.status = "deleting"
                enqueue(session, "disk.wipe", {"disk_id": disk.id})
                changed += 1
                logger.warning("disk_arrears_wipe_scheduled", disk_id=disk.id)
    return changed


async def get_disk_by_id_for_user(session: AsyncSession, user_id: int, disk_id: int) -> DataDisk:
    disk = await session.get(DataDisk, disk_id)
    if disk is None or disk.user_id != user_id or disk.status == "deleted":
        raise not_found("数据盘不存在")
    return disk
