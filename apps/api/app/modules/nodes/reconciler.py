"""Node join reconciliation: a Ready node becomes joined once the pool and GPU operand labels are
written successfully.

Includes unlabeled nodes; desired_pool wins over the enrolled pool; K8s writes happen outside the
state-transition transaction.
"""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.gpu_adapter import pool_node_labels
from app.core.k8s import get_orchestrator
from app.core.locks import LockKey, advisory_lock
from app.core.logging import get_logger
from app.core.timeutil import now_utc
from app.modules.nodes.models import NodeEnrollment, NodeSpec
from app.modules.nodes.service import transition_enrollment

logger = get_logger(__name__)

STALE_HEARTBEAT = timedelta(hours=2)
ACTIVE_STATUSES = ("pending", "installing", "rebooting", "joining")


async def reconcile_enrollments_once(sm: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """One reconciliation round (single instance via advisory lock), returns action counts."""
    counts = {"joined": 0, "failed": 0, "expired": 0, "labeled": 0}
    async with advisory_lock(sm, LockKey.NODE_ENROLL_RECONCILER) as got:
        if not got:
            return counts
        orch = get_orchestrator()
        nodes = {n.name: n for n in await orch.list_nodes(include_unlabeled=True)}
        now = now_utc()
        to_label: list[tuple[int, str, str]] = []
        async with sm() as session:
            desired_pools = {
                r.node_name: r.desired_pool
                for r in (
                    await session.execute(
                        select(NodeSpec).where(NodeSpec.desired_pool.is_not(None))
                    )
                ).scalars()
                if r.desired_pool
            }
            rows = list(
                (
                    await session.execute(
                        select(NodeEnrollment)
                        .where(NodeEnrollment.status.in_(ACTIVE_STATUSES))
                        .with_for_update(skip_locked=True)
                    )
                ).scalars()
            )
            for row in rows:
                if row.expires_at < now:
                    transition_enrollment(row, "expired")
                    counts["expired"] += 1
                    continue
                if row.status == "pending":
                    continue
                node = nodes.get(row.node_name or "")
                if row.node_name and node is not None and node.status == "Ready":
                    pool = desired_pools.get(row.node_name) or row.pool
                    to_label.append((row.id, row.node_name, pool))
                    continue
                if row.last_report_at is not None and row.last_report_at < now - STALE_HEARTBEAT:
                    transition_enrollment(
                        row,
                        "failed",
                        error="install timed out / lost (no heartbeat for 2 h); fix and regenerate"
                        " the token",
                    )
                    counts["failed"] += 1
            await session.commit()

        for enrollment_id, node_name, pool in to_label:
            try:
                await orch.set_node_labels(node_name, pool_node_labels(pool))
            except Exception:
                logger.warning("node_join_label_failed", node=node_name, pool=pool)
                continue
            counts["labeled"] += 1
            async with sm() as session:
                row = await session.get(NodeEnrollment, enrollment_id, with_for_update=True)
                if row is None or row.status not in ACTIVE_STATUSES:
                    continue
                transition_enrollment(row, "joined", phase="joined")
                await session.commit()
            counts["joined"] += 1
            logger.info("node_joined", node=node_name, pool=pool)
    if any(counts.values()):
        logger.info("node_enroll_reconcile", **counts)
    return counts
