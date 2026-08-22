"""worker 入口:同一镜像的第二入口。outbox worker 循环 + APScheduler 定时任务。

定时任务全部先抢 pg advisory lock,多副本部署下天然单实例执行。
"""

import asyncio
import contextlib
import os
import signal
import socket
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.db import get_sessionmaker
from app.core.logging import get_logger, setup_logging
from app.core.metrics import WORKER_HEARTBEAT_TS
from app.core.outbox import process_one, reap_stuck_running
from app.core.timeutil import now_utc

logger = get_logger(__name__)

POLL_INTERVAL_SECONDS = 1.0
HEARTBEAT_INTERVAL_SECONDS = 10.0

# K8s liveness:exec 探针检查该文件 mtime。心跳由独立协程触碰,不挂在 outbox 循环上
# ——挂在循环里探的是「当前任务跑完没有」,长任务会让活着的 worker 被 SIGKILL。
HEARTBEAT_FILE = Path(os.environ.get("SUPERDL_WORKER_HEARTBEAT", "/tmp/superdl-worker-heartbeat"))

# /metrics 端口:结算/死信/reconciler 指标产生在 worker 进程内,单独暴露给 PodMonitor 直抓
# (无 Ingress 路由,仅集群内可达)
METRICS_PORT = int(os.environ.get("SUPERDL_WORKER_METRICS_PORT", "9000"))

_stop = asyncio.Event()


def _touch_heartbeat() -> None:
    WORKER_HEARTBEAT_TS.set(now_utc().timestamp())
    with contextlib.suppress(OSError):  # 只读文件系统等场景放弃心跳
        HEARTBEAT_FILE.write_text(now_utc().isoformat())


async def heartbeat_loop() -> None:
    """独立心跳:只证明事件循环还活着,与当前任务耗时无关。"""
    while not _stop.is_set():
        _touch_heartbeat()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_stop.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)


async def outbox_loop(worker_id: str) -> None:
    sm = get_sessionmaker()
    logger.info("outbox_worker_started", worker_id=worker_id)
    while not _stop.is_set():
        try:
            processed = await process_one(sm, worker_id)
        except Exception:
            logger.exception("outbox_loop_error")
            processed = False
        if not processed and not _stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(_stop.wait(), timeout=POLL_INTERVAL_SECONDS)


async def cleanup_expired_rows(sm) -> dict[str, int]:
    """数据保洁(每日):过期验证码/已用 refresh 记录/已完成 outbox/超保留期审计。"""
    from sqlalchemy import text

    from app.core.config import get_settings

    retention = get_settings().audit_retention_days
    stmts = {
        "sms_codes": "DELETE FROM sms_codes WHERE expires_at < now() - interval '7 days'",
        "used_refresh_tokens": "DELETE FROM used_refresh_tokens WHERE expires_at < now()",
        "outbox_done": (
            "DELETE FROM outbox_tasks WHERE status IN ('done', 'discarded') "
            "AND updated_at < now() - interval '7 days'"
        ),
        "audit_log": (
            f"DELETE FROM audit_log WHERE created_at < now() - interval '{retention} days'"
        ),
        # 限流计数:窗口最长 24h(发码日限),留 2 天余量后即为死行
        "rate_limit_counters": (
            "DELETE FROM rate_limit_counters WHERE updated_at < now() - interval '2 days'"
        ),
    }
    counts: dict[str, int] = {}
    async with sm() as session:
        for name, stmt in stmts.items():
            result = await session.execute(text(stmt))
            counts[name] = result.rowcount or 0
        await session.commit()
    if any(counts.values()):
        logger.info("cleanup_expired_rows", **counts)
    return counts


def register_scheduled_jobs(scheduler: AsyncIOScheduler) -> None:
    """各模块定时任务注册(结算/巡检/聚合)。"""
    from app.modules.billing.patrol import balance_patrol
    from app.modules.billing.payment_service import close_expired_orders, reconcile_pending_orders
    from app.modules.billing.reconcile import reconcile_funds
    from app.modules.billing.settlement import settle_daily_disks, settle_due_hours
    from app.modules.catalog.prewarm import prewarm_patrol
    from app.modules.metering.service import aggregate_previous_hour
    from app.modules.nodes.patrol import node_spec_patrol
    from app.modules.nodes.reconciler import reconcile_enrollments_once
    from app.modules.orchestrator.reconciler import reconcile_once

    sm = get_sessionmaker()

    scheduler.add_job(
        reap_stuck_running,
        "interval",
        minutes=5,
        args=[sm],
        id="outbox_reaper",
    )
    scheduler.add_job(
        reconcile_once,
        "interval",
        seconds=30,
        args=[sm],
        id="reconciler",
        max_instances=1,
        coalesce=True,
    )
    # misfire 宽限:APScheduler 默认只有 1 秒,事件循环稍有阻塞就整轮跳过
    scheduler.add_job(
        settle_due_hours,
        "cron",
        minute=2,
        args=[sm],
        id="hourly_settlement",
        coalesce=True,
        misfire_grace_time=1800,
    )
    scheduler.add_job(
        settle_daily_disks,
        "cron",
        hour=0,
        minute=10,
        args=[sm],
        id="daily_disk_settlement",
        coalesce=True,
        misfire_grace_time=3600,
    )
    # 排在日结之后:先出完账,再对「出账 vs 流水」与「余额 vs 流水累计」
    scheduler.add_job(
        reconcile_funds,
        "cron",
        hour=0,
        minute=30,
        args=[sm],
        id="fund_reconcile",
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        aggregate_previous_hour,
        "cron",
        minute=5,
        args=[sm],
        id="usage_aggregation",
        coalesce=True,
        misfire_grace_time=1800,
    )
    scheduler.add_job(
        close_expired_orders,
        "interval",
        minutes=10,
        args=[sm],
        id="close_expired_orders",
        coalesce=True,
    )
    scheduler.add_job(
        reconcile_pending_orders,
        "interval",
        minutes=2,
        args=[sm],
        id="payment_reconcile",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        cleanup_expired_rows,
        "cron",
        hour=19,  # UTC 19 = 北京 03:00 低峰
        minute=0,
        args=[sm],
        id="cleanup_expired_rows",
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        balance_patrol,
        "interval",
        minutes=5,
        args=[sm],
        id="balance_patrol",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        prewarm_patrol,
        "interval",
        seconds=60,
        args=[sm],
        id="prewarm_patrol",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        node_spec_patrol,
        "interval",
        seconds=60,
        args=[sm],
        id="node_spec_patrol",
        max_instances=1,
        coalesce=True,
        next_run_time=now_utc(),  # 立即首跑:shared 档门禁读能力缓存,不能等首个周期
    )
    scheduler.add_job(
        reconcile_enrollments_once,
        "interval",
        seconds=30,
        args=[sm],
        id="node_enroll_reconciler",
        max_instances=1,
        coalesce=True,
    )


async def main() -> None:
    setup_logging()
    from app.core.errors import init_sentry
    from app.main import wire_modules

    init_sentry()
    wire_modules()
    worker_id = f"{socket.gethostname()}-{os.getpid()}"

    from prometheus_client import start_http_server

    start_http_server(METRICS_PORT)
    logger.info("worker_metrics_listening", port=METRICS_PORT)

    # SIGTERM/SIGINT 优雅停机:停调度器 → 让 outbox 循环收尾当前任务后退出
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):  # pragma: no cover - win 兜底
            loop.add_signal_handler(sig, _stop.set)

    scheduler = AsyncIOScheduler(timezone="UTC")
    register_scheduled_jobs(scheduler)
    scheduler.start()
    _touch_heartbeat()  # 起步先落一次,不让探针在首个 interval 前判死
    heartbeat = asyncio.create_task(heartbeat_loop())
    try:
        await outbox_loop(worker_id)
    finally:
        _stop.set()
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
        scheduler.shutdown(wait=False)
        logger.info("worker_shutdown_complete", worker_id=worker_id)


if __name__ == "__main__":
    asyncio.run(main())
