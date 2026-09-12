"""worker 入口:outbox 循环 + APScheduler 定时任务(清单在 workers/jobs.py)。"""

import asyncio
import contextlib
import hashlib
import os
import signal
import socket
import socketserver
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from prometheus_client import make_wsgi_app

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.http import bearer_matches
from app.core.logging import get_logger, setup_logging
from app.core.metrics import WORKER_HEARTBEAT_TS
from app.core.outbox import process_one
from app.core.timeutil import now_utc
from app.wiring import wire_modules
from app.workers.components import current_component, outbox_types_for
from app.workers.jobs import scheduled_jobs_for

logger = get_logger(__name__)

POLL_INTERVAL_SECONDS = 1.0
HEARTBEAT_INTERVAL_SECONDS = 10.0
# 并发领取协程数(claim 是 FOR UPDATE SKIP LOCKED)
OUTBOX_CONCURRENCY = get_settings().worker_outbox_concurrency

# worker_id 长度预算:outbox_tasks.locked_by 为 String(128),留出 lane 后缀(-N)
MAX_WORKER_ID_LEN = 120


def make_worker_id() -> str:
    """worker 标识 `<hostname>-<pid>`,最长 MAX_WORKER_ID_LEN;超预算时保留可读前缀 + 全名哈希。"""
    hostname = socket.gethostname()
    pid = str(os.getpid())
    budget = MAX_WORKER_ID_LEN - len(pid) - 1
    if len(hostname) <= budget:
        return f"{hostname}-{pid}"
    digest = hashlib.sha256(hostname.encode()).hexdigest()[:8]
    return f"{hostname[: budget - 9]}-{digest}-{pid}"


# K8s liveness 心跳文件(exec 探针查 mtime),由独立协程触碰
HEARTBEAT_FILE = Path(get_settings().worker_heartbeat or "/tmp/superdl-worker-heartbeat")

# worker 进程内 /metrics 端口(PodMonitor 直抓)
METRICS_PORT = get_settings().worker_metrics_port

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
    """N 条并发领取协程,按 next_retry_at, id 排序;task_types 非空时按组件过滤。"""
    sm = get_sessionmaker()
    logger.info(
        "outbox_worker_started",
        worker_id=worker_id,
        concurrency=OUTBOX_CONCURRENCY,
        task_types=sorted(task_types) if task_types is not None else "all",
    )

    async def claim_loop(lane: int) -> None:
        # 各 lane 的 worker_id 互不相同(终态写按 locked_by 校验归属)
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

    class _QuietHandler(WSGIRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: ARG002 覆写 stdlib 签名
            return  # 抓取高频,不打 stderr

    class _ThreadingWSGIServer(socketserver.ThreadingMixIn, WSGIServer):
        daemon_threads = True

    httpd = _ThreadingWSGIServer(("0.0.0.0", port), _QuietHandler)
    httpd.set_app(_metrics_wsgi_app(token))
    threading.Thread(target=httpd.serve_forever, daemon=True, name="metrics-httpd").start()


def _timed_job(
    job_id: str, fn: Callable[..., Awaitable[Any]], period_seconds: float
) -> Callable[..., Awaitable[Any]]:
    """包一层耗时观测:单轮超过周期 80% 打 warning。"""

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
    """按 JOBS 清单注册本组件的定时任务(SUPERDL_WORKER_COMPONENT 过滤),每个经 _timed_job
    包耗时观测。"""
    sm = get_sessionmaker()
    for job in scheduled_jobs_for(current_component()):
        kwargs: dict[str, Any] = {
            **job.trigger,
            "args": [sm],
            "id": job.id,
            "coalesce": job.coalesce,
        }
        if job.max_instances is not None:
            kwargs["max_instances"] = job.max_instances
        if job.misfire_grace_time is not None:
            kwargs["misfire_grace_time"] = job.misfire_grace_time
        if job.run_immediately:
            kwargs["next_run_time"] = now_utc()
        scheduler.add_job(_timed_job(job.id, job.fn, job.period_seconds), **kwargs)


async def main() -> None:
    setup_logging()
    wire_modules()
    worker_id = make_worker_id()
    component = current_component()  # 非法值在此即炸(fail-closed),不带病起跑
    logger.info("worker_component_resolved", component=component.value)

    _start_metrics_server(METRICS_PORT, get_settings().metrics_token)
    logger.info("worker_metrics_listening", port=METRICS_PORT)

    # SIGTERM/SIGINT 优雅停机:停调度器 → outbox 循环收尾当前任务后退出
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
