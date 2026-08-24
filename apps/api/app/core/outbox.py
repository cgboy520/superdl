"""事务性 outbox:改 DB + 动外部系统(K8s 等)时,业务写入与 enqueue() 必须同一
PostgreSQL 事务提交;worker 异步领取执行,失败指数退避,超限进 dead 并告警。

领取协议(短事务三段式,免长锁):
  1. claim:FOR UPDATE SKIP LOCKED 选中 pending 且到期的任务 → status=running,commit
  2. 执行 handler(独立事务;handler 必须幂等)
  3. 成功 → done;失败 → retries+1、指数退避回 pending,超过 max_retries → dead
崩溃遗留的 running 行由 reaper 按 locked_at 超时打回 pending。
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, cast

from sqlalchemy import CursorResult, String, Text, func, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.logging import get_logger
from app.core.metrics import OUTBOX_DEAD_TOTAL, OUTBOX_TASK_TIMEOUT_TOTAL
from app.core.timeutil import now_utc

logger = get_logger(__name__)

MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 10
BACKOFF_MAX_SECONDS = 600  # 退避上限
# 单个 handler 的执行上限;超时取消的只是协程 —— 底层同步线程(如 K8s 客户端调用)
# 无法被中途杀死,会跑完但结果被丢弃;操作幂等,退避重试是安全的
TASK_TIMEOUT_SECONDS = 600.0
# reaper:running 超时打回 pending。必须显著大于 TASK_TIMEOUT_SECONDS,
# 任务还在正常执行时绝不允许被别的副本认领(否则同一任务双写终态),取 2 倍
RUNNING_TIMEOUT = timedelta(seconds=2 * TASK_TIMEOUT_SECONDS)
# 按任务类型的执行超时覆盖(秒):纯删除/通知类快操作不该占满全局上限。
# 未列出的类型用 TASK_TIMEOUT_SECONDS;handler 归属模块不改,映射集中在这里
TASK_TIMEOUT_OVERRIDES: dict[str, float] = {
    "instance.stop": 180.0,
    "instance.release": 300.0,
    "disk.wipe": 120.0,
    "image.prewarm": 120.0,
    "node.cordon": 120.0,
    "notify.sms": 60.0,
}


@dataclass(frozen=True)
class RetryPolicy:
    """按任务类型的重试预算。默认 5 次 × 10s 指数退避 ≈ 5 分钟。

    「等外部作业完成」型任务(如 disk.wipe 轮询集群 Job)须显式放宽预算。
    """

    max_retries: int = MAX_RETRIES
    backoff_base_seconds: int = BACKOFF_BASE_SECONDS
    backoff_max_seconds: int = BACKOFF_MAX_SECONDS


DEFAULT_RETRY_POLICY = RetryPolicy()
_retry_policies: dict[str, RetryPolicy] = {}


def retry_policy_for(task_type: str) -> RetryPolicy:
    return _retry_policies.get(task_type, DEFAULT_RETRY_POLICY)


class OutboxTask(Base):
    __tablename__ = "outbox_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True
    )  # pending / running / done / dead / discarded(管理端人工忽略)
    retries: Mapped[int] = mapped_column(default=0)
    next_retry_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    locked_by: Mapped[str | None] = mapped_column(String(64))
    locked_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


Handler = Callable[[AsyncSession, "OutboxTask"], Awaitable[None]]

# 单次执行的结局:done 成功;retry 失败但预算未尽、退避回 pending;dead 预算耗尽
Outcome = Literal["done", "retry", "dead"]

_registry: dict[str, Handler] = {}

# payload 内的请求链键:enqueue 时把发起请求的 request_id 带进来,执行时回填日志上下文
REQUEST_ID_KEY = "_request_id"


def _current_request_id() -> str | None:
    import structlog

    value = structlog.contextvars.get_contextvars().get("request_id")
    return str(value) if value else None


def outbox_handler(
    task_type: str, *, retry: RetryPolicy | None = None
) -> Callable[[Handler], Handler]:
    """注册 outbox 任务处理器。handler 必须幂等(至少一次执行);retry 覆盖该类型的重试预算。"""

    def deco(fn: Handler) -> Handler:
        if task_type in _registry:
            raise RuntimeError(f"duplicate outbox handler: {task_type}")
        _registry[task_type] = fn
        if retry is not None:
            _retry_policies[task_type] = retry
        return fn

    return deco


def enqueue(session: AsyncSession, task_type: str, payload: dict[str, Any]) -> OutboxTask:
    """入队。不 commit —— 调用方必须把它放进业务事务。

    跨进程请求链:当前 contextvar 的 request_id 随 payload 落库(_request_id 键),
    worker 执行 handler 时回填日志上下文(API 请求与异步执行日志可按同一 id 串联)。
    """
    if REQUEST_ID_KEY not in payload and (request_id := _current_request_id()):
        payload = {**payload, REQUEST_ID_KEY: request_id}
    task = OutboxTask(type=task_type, payload=payload)
    session.add(task)
    return task


async def _claim_one(
    session: AsyncSession, worker_id: str, task_types: frozenset[str] | None = None
) -> OutboxTask | None:
    """领取一个到期任务。task_types 非空时按组件过滤(P1-18):过滤是取数层语义,
    其它组件的任务对本次查询不可见(不会被误领,也不会被排序阻塞)。"""
    stmt = (
        select(OutboxTask)
        .where(OutboxTask.status == "pending", OutboxTask.next_retry_at <= now_utc())
        .order_by(OutboxTask.next_retry_at, OutboxTask.id)  # 到期最早优先,id 决胜
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if task_types is not None:
        stmt = stmt.where(OutboxTask.type.in_(sorted(task_types)))
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    row.status = "running"
    row.locked_by = worker_id
    row.locked_at = now_utc()
    await session.commit()
    return row


async def _process_one(
    sm: async_sessionmaker[AsyncSession],
    worker_id: str = "worker-0",
    task_types: frozenset[str] | None = None,
) -> Outcome | None:
    """领取并执行一个任务。返回执行结局;无任务可领返回 None。"""
    async with sm() as session:
        task = await _claim_one(session, worker_id, task_types)
    if task is None:
        return None

    policy = retry_policy_for(task.type)
    attempt = task.retries + 1
    will_retry = attempt <= policy.max_retries
    timeout = TASK_TIMEOUT_OVERRIDES.get(task.type, TASK_TIMEOUT_SECONDS)
    error: str | None = None
    # 回填发起请求的 request_id(handler 内日志与 API 侧同一请求链);
    # contextvar 按 asyncio 任务隔离,并发 lane 互不污染,执行完即解绑
    request_id = task.payload.get(REQUEST_ID_KEY)
    try:
        handler = _registry.get(task.type)
        if handler is None:
            raise RuntimeError(f"no handler for outbox task type: {task.type}")
        import structlog

        if request_id:
            structlog.contextvars.bind_contextvars(request_id=str(request_id))
        try:
            async with sm() as session:
                await asyncio.wait_for(handler(session, task), timeout=timeout)
                await session.commit()
        finally:
            if request_id:
                structlog.contextvars.unbind_contextvars("request_id")
    except TimeoutError as exc:
        error = f"TimeoutError: handler exceeded {timeout:.0f}s"
        OUTBOX_TASK_TIMEOUT_TOTAL.labels(task_type=task.type).inc()
        if will_retry:
            # 预期内重试(队头卡死退化为一次可重试失败):warning,不带 traceback
            logger.warning(
                "outbox_task_timeout_retry",
                task_id=task.id,
                task_type=task.type,
                attempt=attempt,
                max_retries=policy.max_retries,
                error=error,
            )
        else:
            logger.error(
                "outbox_task_timeout", task_id=task.id, task_type=task.type, error=str(exc)
            )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        if will_retry:
            # 「还没完成」型任务(disk.wipe 轮询、restart 跨优雅期)用抛错表达重试,
            # 属正常控制流:warning 不带 traceback;预算耗尽的最后一次才记 ERROR
            logger.warning(
                "outbox_task_retry",
                task_id=task.id,
                task_type=task.type,
                attempt=attempt,
                max_retries=policy.max_retries,
                error=error,
            )
        else:
            logger.exception("outbox_task_failed", task_id=task.id, task_type=task.type)

    outcome: Outcome = "done" if error is None else ("retry" if will_retry else "dead")
    if outcome == "done":
        new_values: dict[str, Any] = {"status": "done"}
    elif outcome == "retry":
        backoff = timedelta(
            seconds=min(
                policy.backoff_base_seconds * 2 ** (attempt - 1),
                policy.backoff_max_seconds,
            )
        )
        new_values = {
            "status": "pending",
            "retries": attempt,
            "next_retry_at": now_utc() + backoff,
            "last_error": error,
        }
    else:
        new_values = {"status": "dead", "retries": attempt, "last_error": error}

    async with sm() as session:
        # 终态/回退写都必须仍由本 worker 持有该任务:执行期 reaper 可能已回收并交给
        # 其它副本;rowcount==0 = 已有主副本接管,本次结果直接放弃,不得覆盖
        result = cast(
            CursorResult[Any],
            await session.execute(
                update(OutboxTask)
                .where(
                    OutboxTask.id == task.id,
                    OutboxTask.status == "running",
                    OutboxTask.locked_by == worker_id,
                )
                .values(**new_values)
            ),
        )
        await session.commit()
    if result.rowcount == 0:
        logger.warning(
            "outbox_task_ownership_lost", task_id=task.id, task_type=task.type, worker_id=worker_id
        )
    elif outcome == "dead":
        logger.error("outbox_task_dead", task_id=task.id, task_type=task.type, error=error)
        OUTBOX_DEAD_TOTAL.labels(task_type=task.type).inc()
    return outcome


async def process_one(
    sm: async_sessionmaker[AsyncSession],
    worker_id: str = "worker-0",
    task_types: frozenset[str] | None = None,
) -> bool:
    """领取并执行一个任务。返回是否有任务被处理。"""
    return await _process_one(sm, worker_id, task_types) is not None


async def drain(
    sm: async_sessionmaker[AsyncSession],
    *,
    limit: int = 100,
    task_types: frozenset[str] | None = None,
) -> int:
    """连续处理直到队列空(或到 limit)。仅测试用:worker 关停不做冲刷
    (SIGTERM 直接停在跑任务,遗留 running 由 reaper 超时打回 pending)。"""
    n = 0
    while n < limit and await process_one(sm, task_types=task_types):
        n += 1
    return n


class OutboxDrainError(RuntimeError):
    """drain_strict 冲刷到未成功的任务(dead 或退避回 pending),携带 (done, failed) 计数。"""

    def __init__(self, done_count: int, failed_count: int) -> None:
        self.done_count = done_count
        self.failed_count = failed_count
        super().__init__(f"outbox drain 未全成功: done={done_count}, failed={failed_count}")


async def drain_strict(
    sm: async_sessionmaker[AsyncSession], *, limit: int = 100
) -> tuple[int, int]:
    """drain 的严格变体:返回 (done_count, failed_count);任一任务未成功
    (dead,或失败退避回 pending 等下轮)即抛 OutboxDrainError。

    drain 只报告「处理了几个」,失败任务会静默滑进重试;测试需要断言
    「队列不仅被处理而且全部成功」时换用本函数。
    """
    done = failed = 0
    while done + failed < limit:
        outcome = await _process_one(sm)
        if outcome is None:
            break
        if outcome == "done":
            done += 1
        else:
            failed += 1
    if failed:
        raise OutboxDrainError(done, failed)
    return done, failed


async def reap_stuck_running(sm: async_sessionmaker[AsyncSession]) -> int:
    """把超时的 running 任务打回 pending(worker 崩溃遗留)。定时任务调用。

    复活必须计一次失败(retries+1 + 指数退避,预算耗尽进 dead):
    杀进程的任务(OOM、段错误)若不计数,每 5 分钟被无限重投,
    永远进不了 dead 队列、不触发 OUTBOX_DEAD_TOTAL、不上管理端。
    """
    async with sm() as session:
        rows = list(
            (
                await session.execute(
                    select(OutboxTask)
                    .where(
                        OutboxTask.status == "running",
                        OutboxTask.locked_at < now_utc() - RUNNING_TIMEOUT,
                    )
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
        )
        for task in rows:
            policy = retry_policy_for(task.type)
            attempt = task.retries + 1
            if attempt <= policy.max_retries:
                backoff = timedelta(
                    seconds=min(
                        policy.backoff_base_seconds * 2 ** (attempt - 1),
                        policy.backoff_max_seconds,
                    )
                )
                task.status = "pending"
                task.retries = attempt
                task.next_retry_at = now_utc() + backoff
                task.last_error = "reaped: running timeout (worker lost)"
            else:
                task.status = "dead"
                task.retries = attempt
                task.last_error = "reaped: running timeout (worker lost)"
                OUTBOX_DEAD_TOTAL.labels(task_type=task.type).inc()
            task.locked_by = None
            task.locked_at = None
        await session.commit()
        if rows:
            logger.warning("outbox_reaped_stuck_tasks", count=len(rows))
        return len(rows)
