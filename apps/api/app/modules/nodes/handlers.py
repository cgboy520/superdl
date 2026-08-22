"""nodes 模块 outbox 任务处理器。K8s 副作用在这里发生,全部幂等。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.k8s import get_orchestrator
from app.core.logging import get_logger
from app.core.outbox import OutboxTask, outbox_handler

logger = get_logger(__name__)


@outbox_handler("node.cordon")
async def handle_node_cordon(session: AsyncSession, task: OutboxTask) -> None:
    """cordon/uncordon(payload: node_name, unschedulable, reason)。
    幂等:重复 patch 同值无副作用;节点不存在时 K8s 报 404,退避重试后进死信。"""
    node_name = task.payload["node_name"]
    unschedulable = task.payload["unschedulable"]
    await get_orchestrator().set_node_unschedulable(node_name, unschedulable)
    logger.info(
        "node_cordon_applied",
        node=node_name,
        unschedulable=unschedulable,
        reason=task.payload.get("reason"),
    )
