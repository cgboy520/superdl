"""outbox task handlers of the nodes module. K8s side effects happen here, all idempotent."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.gpu_adapter import pool_node_labels
from app.core.k8s import get_orchestrator
from app.core.logging import get_logger
from app.core.outbox import OutboxTask, RetryPolicy, outbox_handler
from app.modules.nodes.models import NodeSpec

logger = get_logger(__name__)


@outbox_handler("node.cordon", retry=RetryPolicy(timeout_seconds=120))
async def handle_node_cordon(session: AsyncSession, task: OutboxTask) -> None:
    """Converge cordon from the inventory's desired_unschedulable; return without a desired state,
    retry when the node is missing."""
    node_name = task.payload["node_name"]
    row = (
        await session.execute(select(NodeSpec).where(NodeSpec.node_name == node_name))
    ).scalar_one_or_none()
    if row is None or row.desired_unschedulable is None:
        logger.warning("node_cordon_no_desired_state", node=node_name, task_id=task.id)
        return
    await get_orchestrator().set_node_unschedulable(node_name, row.desired_unschedulable)
    logger.info(
        "node_cordon_applied",
        node=node_name,
        unschedulable=row.desired_unschedulable,
        reason=task.payload.get("reason"),
    )


@outbox_handler("node.switch_pool", retry=RetryPolicy(timeout_seconds=120))
async def handle_node_switch_pool(session: AsyncSession, task: OutboxTask) -> None:
    """From the inventory's desired_pool: stop scheduling first, then apply the pool and GPU operand
    labels; return without a desired state."""
    node_name = task.payload["node_name"]
    row = (
        await session.execute(select(NodeSpec).where(NodeSpec.node_name == node_name))
    ).scalar_one_or_none()
    if row is None or not row.desired_pool:
        logger.warning("node_switch_pool_no_desired_state", node=node_name, task_id=task.id)
        return
    orch = get_orchestrator()
    await orch.set_node_unschedulable(node_name, True)
    await orch.set_node_labels(node_name, pool_node_labels(row.desired_pool))
    logger.warning(
        "node_switch_pool_applied",
        node=node_name,
        to_pool=row.desired_pool,
        reason=task.payload.get("reason"),
    )


@outbox_handler("node.decommission")
async def handle_node_decommission(session: AsyncSession, task: OutboxTask) -> None:  # noqa: ARG001
    """K8s side of node decommissioning: cordon, then delete the Node object (delete_node); reads
    the payload (one-way terminal state);
    a node already gone from the cluster counts as success."""
    node_name = task.payload["node_name"]
    await get_orchestrator().delete_node(node_name)
    logger.warning("node_decommission_applied", node=node_name, reason=task.payload.get("reason"))
