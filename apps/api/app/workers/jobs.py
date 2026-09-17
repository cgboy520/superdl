"""定时任务清单及组件过滤;workers/main.py 据此注册 APScheduler。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.outbox import reap_stuck_running, report_pending_metrics
from app.core.timeutil import billing_zone
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
from app.workers.cleanup import cleanup_expired_rows
from app.workers.components import WorkerComponent

JobFn = Callable[[async_sessionmaker[AsyncSession]], Awaitable[Any]]


@dataclass(frozen=True)
class ScheduledJob:
    id: str
    fn: JobFn
    component: WorkerComponent
    period_seconds: float
    trigger: dict[str, Any]
    max_instances: int | None = None
    coalesce: bool = True
    misfire_grace_time: int | None = None
    run_immediately: bool = False


def _interval(**kwargs: int) -> dict[str, Any]:
    return {"trigger": "interval", **kwargs}


def _cron(**kwargs: int) -> dict[str, Any]:
    return {"trigger": "cron", **kwargs}


def _daily_local(hour: int, minute: int) -> dict[str, Any]:
    """Once a day at a wall-clock time in the billing zone (the scheduler itself runs in UTC)."""
    return {"trigger": "cron", "hour": hour, "minute": minute, "timezone": billing_zone()}


CORE, TENANT_MGR, NODE_MGR, PREWARM = (
    WorkerComponent.CORE,
    WorkerComponent.TENANT_MGR,
    WorkerComponent.NODE_MGR,
    WorkerComponent.PREWARM,
)

JOBS: tuple[ScheduledJob, ...] = (
    ScheduledJob(
        "outbox_reaper", reap_stuck_running, CORE, 300, _interval(minutes=5), coalesce=False
    ),
    ScheduledJob("outbox_metrics", report_pending_metrics, CORE, 60, _interval(seconds=60)),
    ScheduledJob(
        "reconciler", reconcile_once, TENANT_MGR, 30, _interval(seconds=30), max_instances=1
    ),
    ScheduledJob(
        "hourly_settlement",
        settle_due_hours,
        CORE,
        3600,
        _cron(minute=2),
        misfire_grace_time=1800,
    ),
    ScheduledJob(
        "daily_disk_settlement",
        settle_daily_disks,
        CORE,
        86400,
        _daily_local(0, 10),
        misfire_grace_time=3600,
    ),
    ScheduledJob(
        "fund_reconcile",
        reconcile_funds,
        CORE,
        86400,
        _daily_local(0, 30),
        misfire_grace_time=3600,
    ),
    ScheduledJob(
        "usage_aggregation",
        aggregate_previous_hour,
        CORE,
        3600,
        _cron(minute=5),
        misfire_grace_time=1800,
    ),
    ScheduledJob("close_expired_orders", close_expired_orders, CORE, 600, _interval(minutes=10)),
    ScheduledJob(
        "payment_reconcile",
        reconcile_pending_orders,
        CORE,
        120,
        _interval(minutes=2),
        max_instances=1,
    ),
    ScheduledJob(
        "cleanup_expired_rows",
        cleanup_expired_rows,
        CORE,
        86400,
        _daily_local(3, 0),
        misfire_grace_time=3600,
    ),
    ScheduledJob(
        "balance_patrol", balance_patrol, CORE, 300, _interval(minutes=5), max_instances=1
    ),
    ScheduledJob(
        "prewarm_patrol", prewarm_patrol, PREWARM, 60, _interval(seconds=60), max_instances=1
    ),
    ScheduledJob(
        "node_spec_patrol",
        node_spec_patrol,
        NODE_MGR,
        60,
        _interval(seconds=60),
        max_instances=1,
        run_immediately=True,
    ),
    ScheduledJob(
        "node_enroll_reconciler",
        reconcile_enrollments_once,
        NODE_MGR,
        30,
        _interval(seconds=30),
        max_instances=1,
    ),
    ScheduledJob(
        "subscription_patrol",
        subscription_patrol,
        CORE,
        1800,
        _interval(minutes=30),
        max_instances=1,
    ),
    ScheduledJob(
        "ticket_stale_patrol",
        stale_ticket_patrol,
        CORE,
        1800,
        _interval(minutes=30),
        max_instances=1,
    ),
)

if len({j.id for j in JOBS}) != len(JOBS):
    raise RuntimeError("定时任务 id 重复")


def scheduled_jobs_for(component: WorkerComponent) -> tuple[ScheduledJob, ...]:
    """组件应注册的定时任务;ALL 为全部。"""
    if component is WorkerComponent.ALL:
        return JOBS
    return tuple(j for j in JOBS if j.component is component)
