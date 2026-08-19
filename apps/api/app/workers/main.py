"""worker 入口:同一镜像的第二入口。outbox worker 循环 + APScheduler 定时任务。

定时任务全部先抢 pg advisory lock,多副本部署下天然单实例执行。
"""

import asyncio
import os
import socket

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.db import get_sessionmaker
from app.core.logging import get_logger, setup_logging
from app.core.outbox import process_one, reap_stuck_running

logger = get_logger(__name__)

POLL_INTERVAL_SECONDS = 1.0


async def outbox_loop(worker_id: str) -> None:
    sm = get_sessionmaker()
    logger.info("outbox_worker_started", worker_id=worker_id)
    while True:
        try:
            processed = await process_one(sm, worker_id)
        except Exception:
            logger.exception("outbox_loop_error")
            processed = False
        if not processed:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)


def register_scheduled_jobs(scheduler: AsyncIOScheduler) -> None:
    """各模块定时任务注册。随 WP 推进逐个接入(结算/巡检/聚合)。"""
    from app.modules.billing.patrol import balance_patrol
    from app.modules.billing.settlement import settle_previous_hour
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
    scheduler.add_job(
        settle_previous_hour,
        "cron",
        minute=2,
        args=[sm],
        id="hourly_settlement",
        coalesce=True,
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


async def main() -> None:
    setup_logging()
    from app.main import wire_modules

    wire_modules()
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    scheduler = AsyncIOScheduler(timezone="UTC")
    register_scheduled_jobs(scheduler)
    scheduler.start()
    await outbox_loop(worker_id)


if __name__ == "__main__":
    asyncio.run(main())
