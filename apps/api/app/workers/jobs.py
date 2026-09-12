"""定时任务清单(单一事实源):id、执行函数、归属组件、触发器与周期。
`workers/main.py` 据此注册 APScheduler,`components.scheduled_jobs_for` 据此分片;
新增定时任务只在这里加一行,并给它归属组件(K8s 权限随组件收窄)。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.outbox import reap_stuck_running, report_pending_metrics
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
    period_seconds: float  # 单轮超过其 80% 打 warning(_timed_job)
    trigger: dict[str, Any]  # APScheduler 触发器参数:{"trigger": "interval", "minutes": 5} 或 cron
    max_instances: int | None = None  # 上一轮未完不并发起第二轮
    coalesce: bool = True  # 积压的多次触发合并成一次
    misfire_grace_time: int | None = None  # misfire 宽限(APScheduler 默认 1 秒)
    run_immediately: bool = False  # 启动即首跑


def _interval(**kwargs: int) -> dict[str, Any]:
    return {"trigger": "interval", **kwargs}


def _cron(**kwargs: int) -> dict[str, Any]:
    return {"trigger": "cron", **kwargs}


CORE, TENANT_MGR, NODE_MGR, PREWARM = (
    WorkerComponent.CORE,
    WorkerComponent.TENANT_MGR,
    WorkerComponent.NODE_MGR,
    WorkerComponent.PREWARM,
)

# 定时任务先抢 pg advisory lock 单实例执行(例外:outbox_reaper / close_expired_orders /
# cleanup_expired_rows 为条件 UPDATE/DELETE,不抢锁)。日界类任务按北京日(UTC 16:xx)。
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
    # UTC 16:10 = 北京 00:10:盘费日界按北京日(见 timeutil.billing_day_floor)
    ScheduledJob(
        "daily_disk_settlement",
        settle_daily_disks,
        CORE,
        86400,
        _cron(hour=16, minute=10),
        misfire_grace_time=3600,
    ),
    # 排在日结之后:UTC 16:30 = 北京 00:30
    ScheduledJob(
        "fund_reconcile",
        reconcile_funds,
        CORE,
        86400,
        _cron(hour=16, minute=30),
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
    # UTC 19 = 北京 03:00 低峰
    ScheduledJob(
        "cleanup_expired_rows",
        cleanup_expired_rows,
        CORE,
        86400,
        _cron(hour=19, minute=0),
        misfire_grace_time=3600,
    ),
    ScheduledJob(
        "balance_patrol", balance_patrol, CORE, 300, _interval(minutes=5), max_instances=1
    ),
    ScheduledJob(
        "prewarm_patrol", prewarm_patrol, PREWARM, 60, _interval(seconds=60), max_instances=1
    ),
    # 立即首跑:shared 档门禁读能力缓存,不能等首个周期
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
    # 包周期到期链路:预警 → 自动续费 → 到期停机 → 冻结(回收仍由 balance_patrol 做)
    ScheduledJob(
        "subscription_patrol",
        subscription_patrol,
        CORE,
        1800,
        _interval(minutes=30),
        max_instances=1,
    ),
    # 工单滞留巡检:pending_staff 超 24h → admin_alerts warning
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
