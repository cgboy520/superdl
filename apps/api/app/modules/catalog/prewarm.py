"""镜像预热:outbox handler + 巡检,让 is_prewarmed 成为真实状态。

分工:
- 巡检 prewarm_patrol(worker 60s):铺行(期望集 = enabled 镜像 × Ready/Cordoned 节点,
  插行与 enqueue 同事务,硬规范 #3)、收敛 pulling(问 K8s Job 状态)、失败退避重试、
  cached 复检(防 kubelet 镜像 GC 后状态失真)、清理(节点消失/镜像禁用)。
- handler image.prewarm:确保该(镜像,节点)的定点拉取 Job 存在,行置 pulling。
  Job 创建即返回不等待 —— 大镜像拉取可达数十分钟,完成态由巡检收敛,
  避免 outbox 5 次退避窗口内等不完进 dead。
新节点 Ready 后由巡检自动纳入(≤60s),无需与节点加入流程显式联动。
"""

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.k8s import get_orchestrator
from app.core.locks import LockKey, try_advisory_lock
from app.core.logging import get_logger
from app.core.metrics import PREWARM_FAILED_TOTAL, PREWARM_NODES
from app.core.outbox import OutboxTask, enqueue, outbox_handler
from app.core.policies import get_effective_policies
from app.core.timeutil import now_utc
from app.modules.catalog.models import ImageNodeCache, PlatformImage

logger = get_logger(__name__)

# failed 行自动重试的节流窗口(防对坏镜像/坏节点风暴式重拉;管理员手动预热不受此限)
FAILED_RETRY_INTERVAL = timedelta(minutes=30)
# 预热覆盖的节点状态:Cordoned 会回役,继续维护缓存;NotReady 保留行但不派新任务
TARGET_NODE_STATUSES = ("Ready", "Cordoned")


@outbox_handler("image.prewarm")
async def handle_image_prewarm(session: AsyncSession, task: OutboxTask) -> None:
    """幂等:行已 cached/已删、镜像已删/已禁用 → 跳过;K8s 报错抛出交退避重试。"""
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
        return  # 行由巡检清理
    await get_orchestrator().prewarm_image(node_name, image.image_ref)
    row.status = "pulling"


async def prewarm_patrol(sm: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """单轮巡检。advisory lock 保证多副本单实例执行。返回动作计数(测试/日志用)。"""
    counts = {"planned": 0, "cached": 0, "failed": 0, "requeued": 0, "removed": 0}
    async with sm() as lock_session, try_advisory_lock(lock_session, LockKey.PREWARM_PATROL) as got:
        if not got:
            return counts
        orch = get_orchestrator()
        nodes = await orch.list_nodes()
        known_nodes = {n.name for n in nodes}
        target_nodes = {n.name for n in nodes if n.status in TARGET_NODE_STATUSES}

        ref_by_id = await _plan(sm, known_nodes, target_nodes, counts)
        await _converge_pulling(sm, ref_by_id, counts)
        await _refresh_metrics(sm)
    if any(counts.values()):
        logger.info("prewarm_patrol", **counts)
    return counts


async def _plan(
    sm: async_sessionmaker[AsyncSession],
    known_nodes: set[str],
    target_nodes: set[str],
    counts: dict[str, int],
) -> dict[int, str]:
    """铺行/清理/重试,全部 DB 写与 enqueue 同一事务。返回 image_id → image_ref。"""
    async with sm() as session:
        policies = await get_effective_policies(session)
        recheck = timedelta(hours=policies.prewarm_recheck_hours)
        images = list((await session.execute(select(PlatformImage))).scalars())
        enabled = {img.id for img in images if img.prewarm_enabled}
        ref_by_id = {img.id: img.image_ref for img in images}
        rows = list((await session.execute(select(ImageNodeCache))).scalars())

        now = now_utc()
        alive: set[tuple[int, str]] = set()
        for row in rows:
            # 清理:节点已消失 / 镜像已禁用(镜像删除由 FK CASCADE 兜底)
            if row.node_name not in known_nodes or row.image_id not in enabled:
                await session.delete(row)
                counts["removed"] += 1
                continue
            alive.add((row.image_id, row.node_name))
            if row.status == "failed" and row.updated_at < now - FAILED_RETRY_INTERVAL:
                row.status = "pending"  # last_error 保留供 UI 展示直至下次收敛
                enqueue(
                    session, "image.prewarm", {"image_id": row.image_id, "node_name": row.node_name}
                )
                counts["requeued"] += 1
            elif (
                row.status == "cached"
                and row.checked_at is not None
                and row.checked_at < now - recheck
            ):
                row.status = "pending"  # 复检:镜像仍在时 Job IfNotPresent 秒完,被 GC 则真实重拉
                enqueue(
                    session, "image.prewarm", {"image_id": row.image_id, "node_name": row.node_name}
                )
                counts["requeued"] += 1
        # 铺行:期望集补缺(行 + 任务同事务)
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
    """对 pulling 行逐一问 K8s Job 状态并收敛。每行独立事务,单行失败不拖垮整轮。"""
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
                    await orch.delete_prewarm_job(row.node_name, ref)  # 重试时重建
                    PREWARM_FAILED_TOTAL.labels(image_ref=ref).inc()
                    counts["failed"] += 1
                elif status.state == "absent":
                    # Job 被 TTL 清理或创建丢失:回 pending 重派
                    row.status = "pending"
                    enqueue(
                        session,
                        "image.prewarm",
                        {"image_id": row.image_id, "node_name": row.node_name},
                    )
                    counts["requeued"] += 1
                # running → 留待下轮
                await session.commit()
        except Exception:
            logger.exception("prewarm_converge_error", row_id=row_id)


async def _refresh_metrics(sm: async_sessionmaker[AsyncSession]) -> None:
    """全量刷新覆盖 Gauge(clear 防已删镜像/状态残留陈旧序列)。"""
    async with sm() as session:
        rows = (
            await session.execute(
                select(PlatformImage.image_ref, ImageNodeCache.status, func.count())
                .join(ImageNodeCache, ImageNodeCache.image_id == PlatformImage.id)
                .group_by(PlatformImage.image_ref, ImageNodeCache.status)
            )
        ).all()
    PREWARM_NODES.clear()
    for image_ref, status, count in rows:
        PREWARM_NODES.labels(image_ref=image_ref, status=status).set(count)
