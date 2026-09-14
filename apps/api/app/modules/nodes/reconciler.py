"""节点加入对账器(30s):节点 Ready 即由平台打上池标签与 GPU operand 标签,打成功才判 joined。

池标签只有平台一个写入方(节点侧不自声明,见 assets/node-join.sh 的 step_agent_config):
装机中的节点处于未打标状态,所以这里必须 include_unlabeled 才看得见它。
两阶段:事务内只做状态迁移与收集待办,K8s 写在事务外逐节点独立 try。
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

# installing/rebooting/joining 超过该时长无心跳判失联
STALE_HEARTBEAT = timedelta(hours=2)
ACTIVE_STATUSES = ("pending", "installing", "rebooting", "joining")


async def reconcile_enrollments_once(sm: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """单轮对账(advisory lock 单实例执行),返回动作计数。"""
    counts = {"joined": 0, "failed": 0, "expired": 0, "labeled": 0}
    async with advisory_lock(sm, LockKey.NODE_ENROLL_RECONCILER) as got:
        if not got:
            return counts
        orch = get_orchestrator()
        # 装机中的节点还没有池标签,必须含未打标节点
        nodes = {n.name: n for n in await orch.list_nodes(include_unlabeled=True)}
        now = now_utc()
        # (enrollment_id, node_name, 权威池);事务内只收集,K8s 写在事务外
        to_label: list[tuple[int, str, str]] = []
        async with sm() as session:
            # 期望池优先于登记池:装机途中被切池时两者不一致,按期望池打,不与巡检 C2 打架
            desired_pools = {
                r.node_name: r.desired_pool
                for r in (
                    await session.execute(
                        select(NodeSpec).where(NodeSpec.desired_pool.is_not(None))
                    )
                ).scalars()
                if r.desired_pool
            }
            # FOR UPDATE + skip_locked:被请求路径锁住的行本轮跳过
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
                # 绝对过期(心跳不延长 expires_at)
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
                        row, "failed", error="安装超时/失联(2h 无心跳),可修复后重新生成令牌"
                    )
                    counts["failed"] += 1
            await session.commit()

        for enrollment_id, node_name, pool in to_label:
            try:
                await orch.set_node_labels(node_name, pool_node_labels(pool))
            except Exception:
                # 打标签失败不推进状态:登记留在 installing,下一轮(30s)重试
                logger.warning("node_join_label_failed", node=node_name, pool=pool)
                continue
            counts["labeled"] += 1
            async with sm() as session:
                row = await session.get(NodeEnrollment, enrollment_id, with_for_update=True)
                if row is None or row.status not in ACTIVE_STATUSES:
                    continue
                # joined 即终态,令牌作废
                transition_enrollment(row, "joined", phase="joined")
                await session.commit()
            counts["joined"] += 1
            logger.info("node_joined", node=node_name, pool=pool)
    if any(counts.values()):
        logger.info("node_enroll_reconcile", **counts)
    return counts
