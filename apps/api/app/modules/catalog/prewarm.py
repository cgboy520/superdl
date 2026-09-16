"""Image prewarm outbox and patrol; targets are enabled images and the Ready / Cordoned nodes of
non-CPU pools."""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.gpu_adapter import POOL_CPU
from app.core.k8s import ensure_registry_pull_secret, get_orchestrator
from app.core.locks import LockKey, advisory_lock
from app.core.logging import get_logger
from app.core.outbox import OutboxTask, RetryPolicy, enqueue, outbox_handler
from app.core.platform_config import get_runtime_config
from app.core.timeutil import now_utc
from app.modules.catalog.models import ImageNodeCache, PlatformImage

logger = get_logger(__name__)

FAILED_RETRY_INTERVAL = timedelta(minutes=30)
PENDING_REQUEUE_TIMEOUT = timedelta(minutes=10)
TARGET_NODE_STATUSES = ("Ready", "Cordoned")


@outbox_handler("image.prewarm", retry=RetryPolicy(timeout_seconds=120))
async def handle_image_prewarm(session: AsyncSession, task: OutboxTask) -> None:
    """Idempotent: row already cached / deleted, image deleted / disabled → skip; K8s errors raise
    for backoff retry."""
    image_id = task.payload["image_id"]
    node_name = task.payload["node_name"]
    row = (
        await session.execute(
            select(ImageNodeCache).where(
                ImageNodeCache.image_id == image_id, ImageNodeCache.node_name == node_name
            )
        )
    ).scalar_one_or_none()
    if row is None or row.status == "cached":
        return
    image = await session.get(PlatformImage, image_id)
    if image is None or not image.prewarm_enabled:
        return
    pull_secret = await ensure_registry_pull_secret(session, get_settings().k8s_platform_namespace)
    await get_orchestrator().prewarm_image(
        node_name, image.image_ref, image_pull_secret=pull_secret
    )
    row.status = "pulling"
    row.cached_ref = image.image_ref


async def prewarm_patrol(sm: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """One patrol round (single instance via advisory lock), returns action counts."""
    counts = {"planned": 0, "cached": 0, "failed": 0, "requeued": 0, "removed": 0}
    async with advisory_lock(sm, LockKey.PREWARM_PATROL) as got:
        if not got:
            return counts
        orch = get_orchestrator()
        nodes = await orch.list_nodes()
        known_nodes = {n.name for n in nodes}
        target_nodes = {
            n.name for n in nodes if n.status in TARGET_NODE_STATUSES and n.pool_label != POOL_CPU
        }

        ref_by_id = await _plan(sm, known_nodes, target_nodes, counts)
        await _converge_pulling(sm, ref_by_id, counts)
    if any(counts.values()):
        logger.info("prewarm_patrol", **counts)
    return counts


async def _plan(
    sm: async_sessionmaker[AsyncSession],
    known_nodes: set[str],
    target_nodes: set[str],
    counts: dict[str, int],
) -> dict[int, str]:
    """Seed rows / clean up / retry, every DB write and enqueue in one transaction. Returns image_id
    → image_ref."""
    async with sm() as session:
        policies = await get_runtime_config(session)
        recheck = timedelta(hours=policies.prewarm_recheck_hours)
        images = list((await session.execute(select(PlatformImage))).scalars())
        enabled = {img.id for img in images if img.prewarm_enabled}
        ref_by_id = {img.id: img.image_ref for img in images}
        rows = list((await session.execute(select(ImageNodeCache))).scalars())

        now = now_utc()
        alive: set[tuple[int, str]] = set()
        for row in rows:
            if row.node_name not in known_nodes or row.image_id not in enabled:
                await session.delete(row)
                counts["removed"] += 1
                continue
            alive.add((row.image_id, row.node_name))
            if row.node_name not in target_nodes:
                continue
            if row.cached_ref is not None and row.cached_ref != ref_by_id.get(row.image_id):
                row.status = "pending"
                row.cached_ref = None
                row.checked_at = None
                enqueue(
                    session, "image.prewarm", {"image_id": row.image_id, "node_name": row.node_name}
                )
                counts["requeued"] += 1
            elif row.status == "failed" and row.updated_at < now - FAILED_RETRY_INTERVAL:
                row.status = "pending"
                enqueue(
                    session, "image.prewarm", {"image_id": row.image_id, "node_name": row.node_name}
                )
                counts["requeued"] += 1
            elif row.status == "pending" and row.updated_at < now - PENDING_REQUEUE_TIMEOUT:
                row.updated_at = now
                enqueue(
                    session, "image.prewarm", {"image_id": row.image_id, "node_name": row.node_name}
                )
                counts["requeued"] += 1
            elif (
                row.status == "cached"
                and row.checked_at is not None
                and row.checked_at < now - recheck
            ):
                row.status = "pending"
                enqueue(
                    session, "image.prewarm", {"image_id": row.image_id, "node_name": row.node_name}
                )
                counts["requeued"] += 1
        for image_id in enabled:
            for node in target_nodes:
                if (image_id, node) not in alive:
                    session.add(ImageNodeCache(image_id=image_id, node_name=node))
                    enqueue(session, "image.prewarm", {"image_id": image_id, "node_name": node})
                    counts["planned"] += 1
        await session.commit()
    return ref_by_id


async def _converge_pulling(
    sm: async_sessionmaker[AsyncSession], ref_by_id: dict[int, str], counts: dict[str, int]
) -> None:
    """Ask K8s for the Job status of each pulling row and converge. One transaction per row, a
    failure does not take the round down."""
    orch = get_orchestrator()
    async with sm() as session:
        ids = list(
            (
                await session.execute(
                    select(ImageNodeCache.id).where(ImageNodeCache.status == "pulling")
                )
            ).scalars()
        )
    for row_id in ids:
        try:
            async with sm() as session:
                row = await session.get(ImageNodeCache, row_id)
                if row is None or row.status != "pulling":
                    continue
                ref = ref_by_id.get(row.image_id)
                if ref is None:
                    continue
                status = await orch.get_prewarm_status(row.node_name, ref)
                if status.state == "succeeded":
                    row.status = "cached"
                    row.checked_at = now_utc()
                    row.last_error = None
                    await orch.delete_prewarm_job(row.node_name, ref)
                    counts["cached"] += 1
                elif status.state == "failed":
                    row.status = "failed"
                    row.last_error = status.message or "prewarm job failed"
                    await orch.delete_prewarm_job(row.node_name, ref)
                    counts["failed"] += 1
                elif status.state == "absent":
                    row.status = "pending"
                    enqueue(
                        session,
                        "image.prewarm",
                        {"image_id": row.image_id, "node_name": row.node_name},
                    )
                    counts["requeued"] += 1
                await session.commit()
        except Exception:
            logger.exception("prewarm_converge_error", row_id=row_id)
