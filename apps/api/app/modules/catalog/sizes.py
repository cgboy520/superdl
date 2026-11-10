"""Image size patrol (image-ops, 5 min, advisory lock): reads from the registry how many
compressed bytes each platform image downloads per CPU architecture, for the console's first-start
estimate. Only images whose download_ref differs from image_ref are read; a failed read is
retried next round."""

import asyncio

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.locks import LockKey, advisory_lock
from app.core.logging import get_logger
from app.core.platform_config import RuntimeConfig, get_runtime_config
from app.core.registry import split_image_ref
from app.core.registry_push import RegistryClient, RegistryPushError, RegistryTarget
from app.modules.catalog.models import PlatformImage

logger = get_logger(__name__)


def measure_download_bytes(image_ref: str, cfg: RuntimeConfig) -> dict[str, int]:
    """Per-architecture compressed bytes of image_ref; the pull robot is sent only to the
    configured registry host, any other registry is read anonymously."""
    host, repository, reference = split_image_ref(image_ref)
    own = bool(cfg.registry_host) and host == cfg.registry_host
    target = RegistryTarget(
        host=host,
        repository=repository,
        username=cfg.registry_robot_name if own else "",
        password=cfg.registry_robot_secret if own else "",
        ca_pem=cfg.registry_ca_pem if own else "",
        actions="pull",
    )
    with RegistryClient(target) as client:
        return client.download_sizes(reference)


async def image_size_patrol(sm: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    counts = {"measured": 0, "failed": 0}
    async with advisory_lock(sm, LockKey.IMAGE_SIZE_PATROL) as got:
        if not got:
            return counts
        async with sm() as session:
            cfg = await get_runtime_config(session)
            todo = (
                await session.execute(
                    select(PlatformImage.id, PlatformImage.image_ref).where(
                        PlatformImage.download_ref.is_distinct_from(PlatformImage.image_ref)
                    )
                )
            ).all()
        for image_id, image_ref in todo:
            try:
                sizes = await asyncio.to_thread(measure_download_bytes, image_ref, cfg)
            except (RegistryPushError, httpx.HTTPError, KeyError, ValueError) as exc:
                counts["failed"] += 1
                logger.warning("image_size_failed", image_id=image_id, error=str(exc)[:200])
                continue
            async with sm() as session:
                img = await session.get(PlatformImage, image_id)
                if img is None or img.image_ref != image_ref:
                    continue
                img.download_bytes = sizes
                img.download_ref = image_ref
                await session.commit()
            counts["measured"] += 1
    if any(counts.values()):
        logger.info("image_size_patrol", **counts)
    return counts
