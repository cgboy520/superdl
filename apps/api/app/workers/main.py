"""worker 入口:同一镜像的第二入口。outbox worker 循环 + APScheduler 定时任务。

除三个对并发天然幂等的任务外,定时任务全部先抢 pg advisory lock,多副本单实例执行
(例外:outbox_reaper/close_expired_orders/cleanup_expired_rows 均为条件 UPDATE/DELETE,
并发同跑只是其中一个副本更新 0 行,不抢锁也安全)。
"""

import asyncio
import contextlib
import os
import signal
import socket
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.logging import get_logger, setup_logging
from app.core.metrics import WORKER_HEARTBEAT_TS
from app.core.outbox import process_one, reap_stuck_running
from app.core.timeutil import now_utc

logger = get_logger(__name__)

POLL_INTERVAL_SECONDS = 1.0
HEARTBEAT_INTERVAL_SECONDS = 10.0
# 并发领取协程数:claim 是 FOR UPDATE SKIP LOCKED,多协程不会重复领取;
# 消除全局串行 FIFO 的队头阻塞(一个慢任务不挡住排在后面的关机请求)
OUTBOX_CONCURRENCY = int(os.environ.get("SUPERDL_OUTBOX_CONCURRENCY", "4"))

# K8s liveness:exec 探针检查该文件 mtime。心跳由独立协程触碰,不挂在 outbox 循环上
# (挂在循环里长任务会让活着的 worker 被 SIGKILL)。
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


async def outbox_loop(worker_id: str, task_types: frozenset[str] | None = None) -> None:
    """N 条并发领取协程(SKIP LOCKED 保证不重复);领取按 next_retry_at, id 公平排序。

    task_types 非空时按组件过滤:其它组件的任务在查询层不可见,不阻塞也不误领。"""
    sm = get_sessionmaker()
    logger.info(
        "outbox_worker_started",
        worker_id=worker_id,
        concurrency=OUTBOX_CONCURRENCY,
        task_types=sorted(task_types) if task_types is not None else "all",
    )

    async def claim_loop(lane: int) -> None:
        # 终态写按 locked_by 校验归属,各 lane 的 worker_id 必须互不相同
        lane_id = f"{worker_id}-{lane}"
        while not _stop.is_set():
            try:
                processed = await process_one(sm, lane_id, task_types)
            except Exception:
                logger.exception("outbox_loop_error")
                processed = False
            if not processed and not _stop.is_set():
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(_stop.wait(), timeout=POLL_INTERVAL_SECONDS)

    await asyncio.gather(*(claim_loop(i) for i in range(OUTBOX_CONCURRENCY)))


def _metrics_wsgi_app(token: str | None) -> Callable[..., Any]:
    """worker /metrics 的 WSGI 应用:与 API 同一 SUPERDL_METRICS_TOKEN Bearer 门禁。"""
    from prometheus_client import make_wsgi_app

    from app.core.http import bearer_matches

    inner = make_wsgi_app()

    def app(environ: dict[str, Any], start_response: Callable[..., Any]) -> Any:
        if token and not bearer_matches(environ.get("HTTP_AUTHORIZATION"), token):
            start_response(
                "401 Unauthorized",
                [("Content-Type", "text/plain"), ("WWW-Authenticate", "Bearer")],
            )
            return [b"unauthorized"]
        return inner(environ, start_response)

    return app


def _start_metrics_server(port: int, token: str | None) -> None:
    """线程内 wsgiref(PodMonitor 直抓;无 Ingress 仅集群内可达,仍要求 Bearer)。"""
    import socketserver
    from wsgiref.simple_server import WSGIRequestHandler, WSGIServer

    class _QuietHandler(WSGIRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return  # 抓取高频,不打 stderr

    class _ThreadingWSGIServer(socketserver.ThreadingMixIn, WSGIServer):
        daemon_threads = True

    httpd = _ThreadingWSGIServer(("0.0.0.0", port), _QuietHandler)
    httpd.set_app(_metrics_wsgi_app(token))
    threading.Thread(target=httpd.serve_forever, daemon=True, name="metrics-httpd").start()


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


def _timed_job(
    job_id: str, fn: Callable[..., Awaitable[Any]], period_seconds: float
) -> Callable[..., Awaitable[Any]]:
    """包一层耗时观测:单轮超过周期 80% 打 warning。

    APScheduler 的 coalesce/misfire 会静默丢弃整轮,耗时逼近周期是唯一可观测前兆。
    """

    async def wrapped(*args: Any) -> Any:
        started = time.monotonic()
        try:
            return await fn(*args)
        finally:
            elapsed = time.monotonic() - started
            if elapsed > 0.8 * period_seconds:
                logger.warning(
                    "scheduled_tick_slow",
                    job=job_id,
                    elapsed_seconds=round(elapsed, 1),
                    period_seconds=period_seconds,
                )

    return wrapped


def register_scheduled_jobs(scheduler: AsyncIOScheduler) -> None:
    """各模块定时任务注册(结算/巡检/聚合)。callable 一律经 _timed_job 包耗时观测。

    按 SUPERDL_WORKER_COMPONENT 过滤:组件进程只注册自己的任务,
    组件映射集中在 workers/components.py;ALL(默认)全量注册。"""
    from app.modules.billing.patrol import balance_patrol
    from app.modules.billing.payment_service import close_expired_orders, reconcile_pending_orders
    from app.modules.billing.reconcile import reconcile_funds
    from app.modules.billing.settlement import settle_daily_disks, settle_due_hours
    from app.modules.billing.subscriptions import subscription_patrol
    from app.modules.catalog.prewarm import prewarm_patrol
    from app.modules.metering.service import aggregate_previous_hour
    from app.modules.nodes.patrol import node_spec_patrol
    from app.modules.nodes.reconciler import reconcile_enrollments_once
    from app.modules.orchestrator.reconciler import reconcile_once
    from app.modules.tickets.patrol import stale_ticket_patrol
    from app.workers.components import current_component, scheduled_jobs_for

    sm = get_sessionmaker()
    component = current_component()
    enabled = scheduled_jobs_for(component)

    def add_job(*args: Any, **kwargs: Any) -> None:
        if enabled is None or kwargs["id"] in enabled:
            scheduler.add_job(*args, **kwargs)  # 必须直呼 scheduler.add_job(写成 add_job 即自递归)

    add_job(
        _timed_job("outbox_reaper", reap_stuck_running, 300),
        "interval",
        minutes=5,
        args=[sm],
        id="outbox_reaper",
    )
    add_job(
        _timed_job("reconciler", reconcile_once, 30),
        "interval",
        seconds=30,
        args=[sm],
        id="reconciler",
        max_instances=1,
        coalesce=True,
    )
    # misfire 宽限:APScheduler 默认只有 1 秒,事件循环稍有阻塞就整轮跳过
    add_job(
        _timed_job("hourly_settlement", settle_due_hours, 3600),
        "cron",
        minute=2,
        args=[sm],
        id="hourly_settlement",
        coalesce=True,
        misfire_grace_time=1800,
    )
    add_job(
        _timed_job("daily_disk_settlement", settle_daily_disks, 86400),
        "cron",
        hour=16,  # UTC 16:10 = 北京 00:10:盘费日界按北京日(见 timeutil.billing_day_floor)
        minute=10,
        args=[sm],
        id="daily_disk_settlement",
        coalesce=True,
        misfire_grace_time=3600,
    )
    # 排在日结之后:先出完账,再对「出账 vs 流水」与「余额 vs 流水累计」
    add_job(
        _timed_job("fund_reconcile", reconcile_funds, 86400),
        "cron",
        hour=16,  # UTC 16:30 = 北京 00:30(北京日界日结之后)
        minute=30,
        args=[sm],
        id="fund_reconcile",
        coalesce=True,
        misfire_grace_time=3600,
    )
    add_job(
        _timed_job("usage_aggregation", aggregate_previous_hour, 3600),
        "cron",
        minute=5,
        args=[sm],
        id="usage_aggregation",
        coalesce=True,
        misfire_grace_time=1800,
    )
    add_job(
        _timed_job("close_expired_orders", close_expired_orders, 600),
        "interval",
        minutes=10,
        args=[sm],
        id="close_expired_orders",
        coalesce=True,
    )
    add_job(
        _timed_job("payment_reconcile", reconcile_pending_orders, 120),
        "interval",
        minutes=2,
        args=[sm],
        id="payment_reconcile",
        max_instances=1,
        coalesce=True,
    )
    add_job(
        _timed_job("cleanup_expired_rows", cleanup_expired_rows, 86400),
        "cron",
        hour=19,  # UTC 19 = 北京 03:00 低峰
        minute=0,
        args=[sm],
        id="cleanup_expired_rows",
        coalesce=True,
        misfire_grace_time=3600,
    )
    add_job(
        _timed_job("balance_patrol", balance_patrol, 300),
        "interval",
        minutes=5,
        args=[sm],
        id="balance_patrol",
        max_instances=1,
        coalesce=True,
    )
    add_job(
        _timed_job("prewarm_patrol", prewarm_patrol, 60),
        "interval",
        seconds=60,
        args=[sm],
        id="prewarm_patrol",
        max_instances=1,
        coalesce=True,
    )
    add_job(
        _timed_job("node_spec_patrol", node_spec_patrol, 60),
        "interval",
        seconds=60,
        args=[sm],
        id="node_spec_patrol",
        max_instances=1,
        coalesce=True,
        next_run_time=now_utc(),  # 立即首跑:shared 档门禁读能力缓存,不能等首个周期
    )
    add_job(
        _timed_job("node_enroll_reconciler", reconcile_enrollments_once, 30),
        "interval",
        seconds=30,
        args=[sm],
        id="node_enroll_reconciler",
        max_instances=1,
        coalesce=True,
    )
    # 包周期到期链路:预警 → 自动续费 → 到期停机 → 冻结(回收仍由 balance_patrol 做)
    add_job(
        _timed_job("subscription_patrol", subscription_patrol, 1800),
        "interval",
        minutes=30,
        args=[sm],
        id="subscription_patrol",
        max_instances=1,
        coalesce=True,
    )
    # 工单滞留巡检:pending_staff 超 24h → admin_alerts warning
    add_job(
        _timed_job("ticket_stale_patrol", stale_ticket_patrol, 1800),
        "interval",
        minutes=30,
        args=[sm],
        id="ticket_stale_patrol",
        max_instances=1,
        coalesce=True,
    )


async def main() -> None:
    setup_logging()
    from app.wiring import wire_modules
    from app.workers.components import current_component, outbox_types_for

    wire_modules()
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    component = current_component()  # 非法值在此即炸(fail-closed),不带病起跑
    logger.info("worker_component_resolved", component=component.value)

    _start_metrics_server(METRICS_PORT, get_settings().metrics_token)
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
        await outbox_loop(worker_id, outbox_types_for(component))
    finally:
        _stop.set()
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
        scheduler.shutdown(wait=False)
        logger.info("worker_shutdown_complete", worker_id=worker_id)


if __name__ == "__main__":
    asyncio.run(main())
