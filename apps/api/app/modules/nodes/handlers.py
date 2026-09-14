"""nodes 模块 outbox 任务处理器。K8s 副作用在这里发生,全部幂等。"""

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
    """cordon/uncordon:执行台账期望态(desired_unschedulable),不读 payload(乱序重试幂等收敛)。
    节点不存在 404,退避重试后进死信。"""
    node_name = task.payload["node_name"]
    row = (
        await session.execute(select(NodeSpec).where(NodeSpec.node_name == node_name))
    ).scalar_one_or_none()
    if row is None or row.desired_unschedulable is None:
        logger.warning("node_cordon_no_desired_state", node=node_name, task_id=task.id)
        return  # 无期望态(台账未收录/行被清理):不重放陈旧 payload
    await get_orchestrator().set_node_unschedulable(node_name, row.desired_unschedulable)
    logger.info(
        "node_cordon_applied",
        node=node_name,
        unschedulable=row.desired_unschedulable,
        reason=task.payload.get("reason"),
    )


@outbox_handler("node.switch_pool", retry=RetryPolicy(timeout_seconds=120))
async def handle_node_switch_pool(session: AsyncSession, task: OutboxTask) -> None:
    """切池的 K8s 侧:先停调度再整套下发池标签与 GPU operand 标签(旧池残留键随之删掉)。
    读台账期望态(desired_pool)而非 payload,乱序重试幂等收敛;60s 巡检 C2 兜底重下发。
    主机侧改造(IOMMU / 驱动 / agent config)不在这里,由运维重跑 node-join 补齐。"""
    node_name = task.payload["node_name"]
    row = (
        await session.execute(select(NodeSpec).where(NodeSpec.node_name == node_name))
    ).scalar_one_or_none()
    if row is None or not row.desired_pool:
        logger.warning("node_switch_pool_no_desired_state", node=node_name, task_id=task.id)
        return  # 无期望态(台账未收录/行被清理):不重放陈旧 payload
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
async def handle_node_decommission(session: AsyncSession, task: OutboxTask) -> None:  # noqa: ARG001 handler 签名
    """节点退役的 K8s 侧:cordon 后删 Node 对象(delete_node);读 payload(单向终态);
    节点已不在集群按成功返回。"""
    node_name = task.payload["node_name"]
    await get_orchestrator().delete_node(node_name)
    logger.warning("node_decommission_applied", node=node_name, reason=task.payload.get("reason"))
