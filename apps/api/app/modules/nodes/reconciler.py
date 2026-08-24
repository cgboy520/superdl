"""节点加入对账器:enrollment 声称的进度 ↔ K8s 实际,每 30s 收敛。

joined 的唯一判据是 K8s 侧出现该节点、Ready、且池标签与登记一致(脚本自报不算数)。
"""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.k8s import get_orchestrator
from app.core.locks import LockKey, try_advisory_lock
from app.core.logging import get_logger
from app.core.timeutil import now_utc
from app.modules.nodes.models import NodeEnrollment
from app.modules.nodes.service import transition_enrollment

logger = get_logger(__name__)

# installing/rebooting/joining 超过该时长无心跳判失联
STALE_HEARTBEAT = timedelta(hours=2)
ACTIVE_STATUSES = ("pending", "installing", "rebooting", "joining")


async def reconcile_enrollments_once(sm: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """单轮对账。advisory lock 保证多副本单实例执行。返回动作计数(测试/日志用)。"""
    counts = {"joined": 0, "failed": 0, "expired": 0}
    async with (
        sm() as lock_session,
        try_advisory_lock(lock_session, LockKey.NODE_ENROLL_RECONCILER) as got,
    ):
        if not got:
            return counts
        nodes = {n.name: n for n in await get_orchestrator().list_nodes()}
        now = now_utc()
        async with sm() as session:
            # FOR UPDATE + skip_locked:与请求路径的 revoke/report 并发时,
            # 被锁行本轮跳过(下轮自愈),避免无条件 UPDATE 覆盖刚提交的吊销
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
                # 绝对过期:心跳只刷新 last_report_at,不延长 expires_at
                if row.expires_at < now:
                    transition_enrollment(row, "expired")
                    counts["expired"] += 1
                    continue
                if row.status == "pending":
                    continue
                node = nodes.get(row.node_name or "")
                if node is not None and node.status == "Ready":
                    if node.pool_label == row.pool:
                        # joined 即终态,令牌随之作废;预热由巡检自动纳入新节点
                        transition_enrollment(row, "joined", phase="joined")
                        counts["joined"] += 1
                    else:
                        transition_enrollment(
                            row,
                            "failed",
                            error=(
                                f"池标签不符:登记 {row.pool},节点实际 {node.pool_label};"
                                "请核查该节点的 config.yaml"
                            ),
                        )
                        counts["failed"] += 1
                    continue
                if row.last_report_at is not None and row.last_report_at < now - STALE_HEARTBEAT:
                    transition_enrollment(
                        row, "failed", error="安装超时/失联(2h 无心跳),可修复后重新生成令牌"
                    )
                    counts["failed"] += 1
            await session.commit()
    if any(counts.values()):
        logger.info("node_enroll_reconcile", **counts)
    return counts
