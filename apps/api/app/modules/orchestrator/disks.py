"""数据盘服务:创建/扩容/删除/挂载管理,独立于实例生命周期。"""

from datetime import timedelta
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
from app.core.policies import get_effective_policies
from app.core.timeutil import now_utc
from app.modules.account import service as account_service
from app.modules.billing import service as billing_service
from app.modules.nodes import service as nodes_service
from app.modules.orchestrator.models import DataDisk, Instance

logger = get_logger(__name__)

BILLABLE_STATUSES = ("active",)  # grace(欠费宽限)停计费,frozen 不计费
# 欠费链路上的全部状态(list_arrears_chain_user_ids 用)
ARREARS_CHAIN_STATUSES = ("active", "grace", "frozen")


async def create_disk(
    session: AsyncSession,
    user_id: int,
    name: str,
    size_gb: int,
    idempotency_key: str | None = None,
) -> tuple[DataDisk, bool]:
    """创建数据盘。返回 (盘, created),created=False = 幂等重放(路由回 200)。"""
    fingerprint = request_fingerprint(user_id, name, size_gb)
    if idempotency_key:
        # 幂等键;同键异参 409
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
    # JuiceFS SC 缺位先拦
    await nodes_service.require_storage_classes(session, with_data_disk=True)
    policies = await get_effective_policies(session)
    if not policies.disk_min_gb <= size_gb <= policies.disk_max_gb:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="disks.sizeRange",
            params={"min": policies.disk_min_gb, "max": policies.disk_max_gb},
        )
    price = as_price(policies.disk_price_gb_month)
    daily = disk_daily_charge(price, size_gb)
    # 临界区:assert_can_afford 锁钱包行持有到 commit;配额与余额校验都在锁内
    await billing_service.assert_can_afford(session, user_id, additional_daily_disk=daily)
    # 数量配额,生效值走 account.get_user_limits
    limits = await account_service.get_user_limits(session, user_id)
    max_disks = limits.max_disks
    live = (
        await session.execute(
            select(func.count())
            .select_from(DataDisk)
            .where(DataDisk.user_id == user_id, DataDisk.status != "deleted")
        )
    ).scalar_one()
    if live >= max_disks:
        raise AppError(
            ErrorCode.VALIDATION_ERROR, key="disks.countQuota", params={"max": max_disks}
        )
    disk_uuid = uuid4().hex
    disk = DataDisk(
        uuid=disk_uuid,
        user_id=user_id,
        name=name,
        size_gb=size_gb,
        juicefs_subpath=f"disk-{disk_uuid}",
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
        # 并发同幂等键:按重放返回既有盘
        return result, False
    # 同事务入队 JuiceFS 目录配额下发;handler 成功才置 quota_synced
    enqueue(session, "disk.quota", {"disk_id": disk.id})
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


async def _settle_pending_days(session: AsyncSession, disk: DataDisk) -> None:
    """按变更前容量结清未出账的自然日(同事务)。非计费态的盘不补账。"""
    if disk.status not in BILLABLE_STATUSES:
        return
    await billing_service.settle_disk_pending_days(
        session,
        disk_id=disk.id,
        user_id=disk.user_id,
        price_gb_month=disk.price_gb_month,
        size_gb=disk.size_gb,
        created_at=disk.created_at,
    )


async def expand_disk(session: AsyncSession, user_id: int, uuid: str, new_size_gb: int) -> DataDisk:
    # FOR UPDATE 锁盘行;锁序 disk → bill → wallet
    disk = (
        await session.execute(
            select(DataDisk)
            .where(DataDisk.uuid == uuid, DataDisk.user_id == user_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if disk is None or disk.status == "deleted":
        raise not_found("数据盘不存在")
    if disk.status != "active":
        raise AppError(ErrorCode.VALIDATION_ERROR, key="disks.expandNeedsActive")
    if new_size_gb <= disk.size_gb:
        raise AppError(ErrorCode.DISK_SHRINK_FORBIDDEN, key="disks.shrinkForbidden")
    max_gb = (await get_effective_policies(session)).disk_max_gb
    if new_size_gb > max_gb:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="disks.sizeMax", params={"max": max_gb})
    await _settle_pending_days(session, disk)  # 先按旧容量结清
    # 增量日费过燃烧率校验(同事务)
    delta_daily = disk_daily_charge(disk.price_gb_month, new_size_gb) - disk_daily_charge(
        disk.price_gb_month, disk.size_gb
    )
    await billing_service.assert_can_afford(session, user_id, additional_daily_disk=delta_daily)
    # 重下发 JuiceFS 目录配额(quota_synced=false 直到 handler 成功)
    disk.size_gb = new_size_gb
    disk.quota_synced = False
    enqueue(session, "disk.quota", {"disk_id": disk.id})
    await session.commit()
    return disk


async def delete_disk(session: AsyncSession, user_id: int, uuid: str) -> DataDisk:
    """删除:挂载中禁止;进入 deleting,由 outbox 擦除后置 deleted。"""
    # FOR UPDATE 锁盘行(与 attach_for_instance 同纪律)
    disk = (
        await session.execute(
            select(DataDisk)
            .where(DataDisk.uuid == uuid, DataDisk.user_id == user_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if disk is None or disk.status == "deleted":
        raise not_found("数据盘不存在")
    if disk.mounted_instance_id is not None:
        # 挂载实例 stopped/frozen/failed 时放行并自动解挂
        inst = await session.get(Instance, disk.mounted_instance_id)
        if inst is not None and inst.status in ("stopped", "frozen", "failed"):
            disk.mounted_instance_id = None
        else:
            raise AppError(ErrorCode.DISK_IN_USE, key="disks.inUseDelete")
    if disk.status == "deleting":
        return disk
    await _settle_pending_days(session, disk)  # 末日账:当日建当日删不能免单
    disk.status = "deleting"
    # 同事务摘除所有实例的挂载引用
    await session.execute(
        update(Instance).where(Instance.data_disk_id == disk.id).values(data_disk_id=None)
    )
    enqueue(session, "disk.wipe", {"disk_id": disk.id})
    await session.commit()
    return disk


async def attach_for_instance(session: AsyncSession, user_id: int, disk_id: int, instance_id: int):
    """实例创建时挂载校验 + 占用(FOR UPDATE 锁盘行)。同事务调用,不 commit。"""
    disk = await session.get(DataDisk, disk_id, with_for_update=True)
    if disk is None or disk.user_id != user_id or disk.status == "deleted":
        raise not_found("数据盘不存在")
    if disk.status != "active":
        raise AppError(ErrorCode.VALIDATION_ERROR, key="disks.notMountable")
    # 配额未下发的盘不得挂载
    if not disk.quota_synced:
        raise conflict(key="disks.quotaNotSynced")
    if disk.mounted_instance_id is not None and disk.mounted_instance_id != instance_id:
        raise AppError(ErrorCode.DISK_IN_USE, key="disks.mountedElsewhere")
    disk.mounted_instance_id = instance_id
    return disk


async def detach_for_instance(session: AsyncSession, instance_id: int) -> None:
    """实例终态(released/failed)时解除挂载。同事务调用。"""
    disk = (
        await session.execute(select(DataDisk).where(DataDisk.mounted_instance_id == instance_id))
    ).scalar_one_or_none()
    if disk is not None:
        disk.mounted_instance_id = None


async def list_arrears_chain_user_ids(session: AsyncSession) -> list[int]:
    """欠费巡检的用户集合:名下有任何一块 active/grace/frozen 盘。"""
    return list(
        (
            await session.execute(
                select(DataDisk.user_id)
                .where(DataDisk.status.in_(ARREARS_CHAIN_STATUSES))
                .distinct()
            )
        ).scalars()
    )


async def list_billable_disks(session: AsyncSession) -> list[DataDisk]:
    return list(
        (
            await session.execute(select(DataDisk).where(DataDisk.status.in_(BILLABLE_STATUSES)))
        ).scalars()
    )


async def arrears_transition_disks(session: AsyncSession, user_id: int, in_arrears: bool) -> int:
    """欠费巡检钩子:active↔grace→frozen→deleting 链路,返回变更数。
    grace_started_at 首次进入宽限后不清零;frozen_started_at 每次进入 frozen 重新起算。
    """
    policies = await get_effective_policies(session)
    now = now_utc()
    changed = 0
    disks = list(
        (
            await session.execute(
                select(DataDisk).where(
                    DataDisk.user_id == user_id,
                    DataDisk.status.in_(ARREARS_CHAIN_STATUSES),
                )
            )
        ).scalars()
    )
    for disk in disks:
        if not in_arrears:
            if disk.status in ("grace", "frozen"):
                disk.status = "active"
                # grace_started_at 保留;frozen_started_at 清零;grace_ended_at 记恢复时刻
                disk.frozen_started_at = None
                disk.grace_ended_at = now
                changed += 1
            continue
        if disk.status == "active":
            await _settle_pending_days(session, disk)  # 进 grace 即停计费:先结清在账天数
            disk.status = "grace"
            if disk.grace_started_at is None:
                disk.grace_started_at = now
            disk.grace_ended_at = None  # 新一段宽限开始,上一段区间作废
            changed += 1
        elif disk.status == "grace" and disk.grace_started_at is not None:
            if now - disk.grace_started_at > timedelta(days=policies.disk_grace_days):
                disk.status = "frozen"
                disk.frozen_started_at = now
                changed += 1
        elif disk.status == "frozen" and disk.frozen_started_at is not None:
            if now - disk.frozen_started_at > timedelta(days=policies.disk_frozen_days):
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


async def lock_disk_for_attach(session: AsyncSession, user_id: int, disk_id: int) -> DataDisk:
    """建实例挂盘前的归属校验 + FOR UPDATE 行锁;锁序 disk → wallet。"""
    disk = await session.get(DataDisk, disk_id, with_for_update=True)
    if disk is None or disk.user_id != user_id or disk.status == "deleted":
        raise not_found("数据盘不存在")
    return disk
