"""Custom image patrol (image-ops, 10 min, advisory lock): drop expired uploads and old failed
rows, fail imports whose task is gone, delete the images of deleted accounts and sweep upload
directories no live upload owns."""

import asyncio
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.locks import LockKey, advisory_lock
from app.core.logging import get_logger
from app.core.outbox import OutboxTask, enqueue
from app.core.timeutil import now_utc
from app.modules.account import service as account_service
from app.modules.customimages import staging
from app.modules.customimages.models import (
    STATUS_DELETING,
    STATUS_FAILED,
    STATUS_IMPORTING,
    STATUS_READY,
    STATUS_UPLOADING,
    CustomImage,
)
from app.modules.customimages.service import DELETE_TASK, IMPORT_TASK

logger = get_logger(__name__)

FAILED_RETENTION = timedelta(days=30)
STUCK_IMPORT = timedelta(hours=5)
STALE_DIR_AGE = timedelta(days=1)


async def _drop(session: AsyncSession, image: CustomImage) -> None:
    await asyncio.to_thread(staging.remove_dir, staging.staging_dir(image.user_id, image.uuid))
    await session.delete(image)


async def _expired_and_old(session: AsyncSession, counts: dict[str, int]) -> None:
    now = now_utc()
    expired = (
        await session.scalars(
            select(CustomImage).where(
                CustomImage.status == STATUS_UPLOADING, CustomImage.upload_expires_at < now
            )
        )
    ).all()
    old = (
        await session.scalars(
            select(CustomImage).where(
                CustomImage.status == STATUS_FAILED,
                CustomImage.updated_at < now - FAILED_RETENTION,
            )
        )
    ).all()
    for image in [*expired, *old]:
        await _drop(session, image)
    counts["dropped"] += len(expired) + len(old)


async def _interrupted(session: AsyncSession, counts: dict[str, int]) -> None:
    stuck = (
        await session.scalars(
            select(CustomImage).where(
                CustomImage.status == STATUS_IMPORTING,
                CustomImage.updated_at < now_utc() - STUCK_IMPORT,
            )
        )
    ).all()
    if not stuck:
        return
    live = {
        int(v)
        for v in (
            await session.scalars(
                select(OutboxTask.payload["image_id"].as_string()).where(
                    OutboxTask.type == IMPORT_TASK, OutboxTask.status.in_(("pending", "running"))
                )
            )
        ).all()
        if v and v.isdigit()
    }
    for image in stuck:
        if image.id in live:
            continue
        image.status = STATUS_FAILED
        image.error_key = "customImages.import.interrupted"
        image.import_finished_at = now_utc()
        await asyncio.to_thread(staging.remove_dir, staging.staging_dir(image.user_id, image.uuid))
        counts["interrupted"] += 1


async def _orphaned_owners(session: AsyncSession, counts: dict[str, int]) -> None:
    rows = (
        await session.scalars(
            select(CustomImage).where(
                CustomImage.status.in_((STATUS_UPLOADING, STATUS_FAILED, STATUS_READY))
            )
        )
    ).all()
    statuses = await account_service.user_statuses(session, {r.user_id for r in rows})
    for image in rows:
        if statuses.get(image.user_id, "deleted") != "deleted":
            continue
        if image.status == STATUS_READY:
            image.status = STATUS_DELETING
            enqueue(session, DELETE_TASK, {"image_id": image.id})
        else:
            await _drop(session, image)
        counts["owner_deleted"] += 1


async def custom_image_patrol(sm: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    counts = {"dropped": 0, "interrupted": 0, "owner_deleted": 0, "stale_dirs": 0}
    async with advisory_lock(sm, LockKey.CUSTOM_IMAGE_PATROL) as got:
        if not got:
            return counts
        async with sm() as session:
            await _expired_and_old(session, counts)
            await _interrupted(session, counts)
            await _orphaned_owners(session, counts)
            await session.commit()
            keep = set(
                (
                    await session.scalars(
                        select(CustomImage.uuid).where(
                            CustomImage.status.in_((STATUS_UPLOADING, STATUS_IMPORTING))
                        )
                    )
                ).all()
            )
        stale = await asyncio.to_thread(staging.stale_dirs, keep, STALE_DIR_AGE)
        for directory in stale:
            await asyncio.to_thread(staging.remove_dir, directory)
        counts["stale_dirs"] = len(stale)
    if any(counts.values()):
        logger.info("custom_image_patrol", **counts)
    return counts
