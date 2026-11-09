"""image-ops outbox handlers: import a staged archive into the registry, delete a registry
artifact. The import streams the archive once in a worker thread; progress lands in the row
every few seconds."""

import asyncio
import contextlib

import httpx
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.core.logging import get_logger
from app.core.outbox import OutboxTask, RetryPolicy, outbox_handler
from app.core.platform_config import RuntimeConfig, get_runtime_config
from app.core.registry_push import (
    RegistryClient,
    RegistryTarget,
    delete_harbor_artifact,
)
from app.core.timeutil import now_utc
from app.modules.customimages import staging
from app.modules.customimages.archive import (
    ChunkReader,
    ImportFailure,
    ImportResult,
    import_archive,
)
from app.modules.customimages.models import (
    STATUS_DELETING,
    STATUS_FAILED,
    STATUS_IMPORTING,
    STATUS_READY,
    CustomImage,
)
from app.modules.customimages.service import DELETE_TASK, GIB, IMPORT_TASK, repository_of

logger = get_logger(__name__)

PROGRESS_SECONDS = 5.0
UNPACKED_FACTOR = 4
# Test injection: the registry transport (None = the network).
registry_transport: httpx.BaseTransport | None = None


def _target(cfg: RuntimeConfig, image: CustomImage) -> RegistryTarget:
    return RegistryTarget(
        host=cfg.registry_host.strip(),
        repository=repository_of(cfg, image),
        username=cfg.registry_push_robot_name,
        password=cfg.registry_push_robot_secret,
        ca_pem=cfg.registry_ca_pem,
    )


def _run_import(
    reader: ChunkReader, archive_format: str, target: RegistryTarget, tag: str, max_unpacked: int
) -> ImportResult:
    with RegistryClient(target, transport=registry_transport) as client:
        return import_archive(reader, archive_format, client, tag, max_unpacked_bytes=max_unpacked)


async def _report_progress(image_id: int, reader: ChunkReader) -> None:
    sm = get_sessionmaker()
    while True:
        await asyncio.sleep(PROGRESS_SECONDS)
        async with sm() as session:
            await session.execute(
                update(CustomImage)
                .where(CustomImage.id == image_id, CustomImage.status == STATUS_IMPORTING)
                .values(progress_bytes=reader.consumed)
            )
            await session.commit()


def _fail(image: CustomImage, key: str, detail: str) -> None:
    image.status = STATUS_FAILED
    image.error_key = f"customImages.import.{key}"
    image.error_detail = detail or None
    image.import_finished_at = now_utc()


def _ready(image: CustomImage, cfg: RuntimeConfig, result: ImportResult) -> None:
    target = _target(cfg, image)
    image.status = STATUS_READY
    image.image_ref = f"{target.host}/{target.repository}:{image.tag}@{result.digest}"
    image.digest = result.digest
    image.size_bytes = result.size_bytes
    image.unpacked_bytes = result.unpacked_bytes
    image.architecture = result.architecture
    image.os = result.os
    image.entrypoint = result.entrypoint
    image.cmd = result.cmd
    image.exposed_ports = result.exposed_ports
    image.dev_ready = result.dev_ready
    image.layers = result.layers
    image.progress_bytes = image.upload_bytes
    image.import_finished_at = now_utc()
    image.error_key = None
    image.error_detail = None


@outbox_handler(IMPORT_TASK, retry=RetryPolicy(max_retries=2, timeout_seconds=7200))
async def handle_import(session: AsyncSession, task: OutboxTask) -> None:
    """An archive that is not an importable image fails the row with a reason (staged chunks are
    dropped); registry or volume errors raise for the outbox to retry."""
    image = await session.get(CustomImage, task.payload["image_id"])
    if image is None or image.status != STATUS_IMPORTING:
        return
    cfg = await get_runtime_config(session)
    directory = staging.staging_dir(image.user_id, image.uuid)
    if not (cfg.registry_host and cfg.registry_push_robot_name and cfg.registry_push_robot_secret):
        _fail(image, "registryNotConfigured", "")
        return
    image.import_started_at = now_utc()
    await session.commit()
    reader = ChunkReader(directory, image.chunk_bytes, image.upload_bytes)
    max_unpacked = cfg.custom_image_max_upload_gb * GIB * UNPACKED_FACTOR
    reporter = asyncio.create_task(_report_progress(image.id, reader))
    try:
        result = await asyncio.to_thread(
            _run_import, reader, image.archive_format, _target(cfg, image), image.tag, max_unpacked
        )
    except ImportFailure as exc:
        _fail(image, exc.key, exc.detail)
        await asyncio.to_thread(staging.remove_dir, directory)
        logger.warning("custom_image_import_rejected", image_uuid=image.uuid, reason=exc.key)
        return
    finally:
        reporter.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reporter
    await session.refresh(image)
    if image.status != STATUS_IMPORTING:
        return
    _ready(image, cfg, result)
    await asyncio.to_thread(staging.remove_dir, directory)
    logger.info("custom_image_imported", image_uuid=image.uuid, digest=result.digest)


@outbox_handler(DELETE_TASK, retry=RetryPolicy(max_retries=8, backoff_base_seconds=30))
async def handle_delete(session: AsyncSession, task: OutboxTask) -> None:
    image = await session.get(CustomImage, task.payload["image_id"])
    if image is None or image.status != STATUS_DELETING:
        return
    if image.digest:
        cfg = await get_runtime_config(session)
        await asyncio.to_thread(
            delete_harbor_artifact,
            _target(cfg, image),
            image.digest,
            transport=registry_transport,
        )
    await asyncio.to_thread(staging.remove_dir, staging.staging_dir(image.user_id, image.uuid))
    await session.delete(image)
    logger.info("custom_image_deleted", image_uuid=image.uuid)
