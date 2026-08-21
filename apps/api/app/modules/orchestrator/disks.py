"""数据盘服务:创建/扩容/删除/挂载管理。独立于实例生命周期(留存抓手)。"""

from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode, not_found
from app.core.logging import get_logger
from app.core.money import as_price, disk_daily_charge
from app.core.outbox import enqueue
from app.core.policies import get_effective_policies
from app.core.timeutil import now_utc
from app.modules.billing import service as billing_service
from app.modules.nodes import service as nodes_service
from app.modules.orchestrator.models import DataDisk

logger = get_logger(__name__)

BILLABLE_STATUSES = ("active", "grace")  # frozen 不再计费
# 欠费链路上的全部状态。巡检口径必须用这一组:用 BILLABLE_STATUSES 会漏掉「盘全部 frozen
# 且已充值」的用户,他们永远不被处理(见 list_arrears_chain_user_ids)。
ARREARS_CHAIN_STATUSES = ("active", "grace", "frozen")


async def create_disk(
    session: AsyncSession,
    user_id: int,
    name: str,
    size_gb: int,
    idempotency_key: str | None = None,
) -> DataDisk:
    if idempotency_key:
        # 与实例创建同款:响应丢失时用户按第二下不会开出第二块按日计费的盘
        existing = (
            await session.execute(
                select(DataDisk).where(
                    DataDisk.user_id == user_id, DataDisk.idempotency_key == idempotency_key
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
    # JuiceFS SC 缺位时先拦下:否则用户买到一块永远挂不上、却按日计费的盘
    await nodes_service.require_storage_classes(session, with_data_disk=True)
    policies = await get_effective_policies(session)
    if not policies.disk_min_gb <= size_gb <= policies.disk_max_gb:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            key="disks.sizeRange",
            params={"min": policies.disk_min_gb, "max": policies.disk_max_gb},
        )
    # 数量配额:建盘只校验余额(日结才扣),不设上限的话一个账号能把 JuiceFS 铺满
    max_disks = get_settings().max_disks_per_user
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
    price = as_price(policies.disk_price_gb_month)
    daily = disk_daily_charge(price, size_gb)
    await billing_service.require_balance_at_least(
        session,
        user_id,
        daily,
        hint_key="billing.insufficientForDisk",
        hint_params={"amount": daily},
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
    disk = await get_disk(session, user_id, uuid)
    if disk.status != "active":
        raise AppError(ErrorCode.VALIDATION_ERROR, key="disks.expandNeedsActive")
    if new_size_gb <= disk.size_gb:
        raise AppError(ErrorCode.DISK_SHRINK_FORBIDDEN, key="disks.shrinkForbidden")
    max_gb = (await get_effective_policies(session)).disk_max_gb
    if new_size_gb > max_gb:
        raise AppError(ErrorCode.VALIDATION_ERROR, key="disks.sizeMax", params={"max": max_gb})
    await _settle_pending_days(session, disk)  # 先按旧容量结清,扩容不追溯涨价
    disk.size_gb = new_size_gb
    await session.commit()
    return disk


async def delete_disk(session: AsyncSession, user_id: int, uuid: str) -> DataDisk:
    """删除(前端多级防护后调用)。挂载中禁止;进入 deleting,由 outbox 擦除后置 deleted。"""
    disk = await get_disk(session, user_id, uuid)
    if disk.mounted_instance_id is not None:
        raise AppError(ErrorCode.DISK_IN_USE, key="disks.inUseDelete")
    if disk.status == "deleting":
        return disk
    await _settle_pending_days(session, disk)  # 末日账:当日建当日删不能免单
    disk.status = "deleting"
    enqueue(session, "disk.wipe", {"disk_id": disk.id})
    await session.commit()
    return disk


async def attach_for_instance(session: AsyncSession, user_id: int, disk_id: int, instance_id: int):
    """实例创建时挂载校验 + 占用。同事务调用,不 commit。

    FOR UPDATE 锁盘行:读-判-写之间无锁时,两个并发创建可把同一块盘挂到两台实例
    (同 subPath 双挂,数据互踩)。
    """
    disk = await session.get(DataDisk, disk_id, with_for_update=True)
    if disk is None or disk.user_id != user_id or disk.status == "deleted":
        raise not_found("数据盘不存在")
    if disk.status != "active":
        raise AppError(ErrorCode.VALIDATION_ERROR, key="disks.notMountable")
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
    """欠费巡检的用户集合:名下有任何一块处于欠费链路上的盘。

    禁止拼成「有 active/grace 盘的用户 ∪ 余额≤0 的用户」:盘已熬到 frozen 而用户刚充了钱时
    两个集合都不命中,他的盘永远解冻不了、也永远不会到期清除。
    """
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
    """欠费巡检钩子:active↔grace→frozen→deleting 链路。返回变更数。"""
    policies = await get_effective_policies(session)
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

            if now - disk.grace_started_at > timedelta(days=policies.disk_grace_days):
                disk.status = "frozen"
                disk.frozen_started_at = now
                changed += 1
        elif disk.status == "frozen" and disk.frozen_started_at is not None:
            from datetime import timedelta

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
