"""事务性 outbox:改 DB + 动外部系统(K8s 等)时,业务写入与 enqueue() 必须同一
PostgreSQL 事务提交;worker 异步领取执行,失败指数退避,超限进 dead 并告警。

领取协议(短事务三段式,免长锁):
  1. claim:FOR UPDATE SKIP LOCKED 选中 pending 且到期的任务 → status=running,commit
  2. 执行 handler(独立事务;handler 必须幂等)
  3. 成功 → done;失败 → retries+1、指数退避回 pending,超过 max_retries → dead
崩溃遗留的 running 行由 reaper 按 locked_at 超时打回 pending。
"""

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, String, Text, func, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.logging import get_logger
from app.core.metrics import OUTBOX_DEAD_TOTAL
from app.core.timeutil import now_utc

logger = get_logger(__name__)

MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 10
RUNNING_TIMEOUT = timedelta(minutes=10)  # reaper:running 超时打回 pending


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

_registry: dict[str, Handler] = {}


def outbox_handler(task_type: str) -> Callable[[Handler], Handler]:
    """注册 outbox 任务处理器。handler 必须幂等(会被至少一次执行)。"""

    def deco(fn: Handler) -> Handler:
        if task_type in _registry:
            raise RuntimeError(f"duplicate outbox handler: {task_type}")
        _registry[task_type] = fn
        return fn

    return deco


def enqueue(session: AsyncSession, task_type: str, payload: dict[str, Any]) -> OutboxTask:
    """入队。不 commit —— 调用方必须把它放进业务事务。"""
    task = OutboxTask(type=task_type, payload=payload)
    session.add(task)
    return task


async def _claim_one(session: AsyncSession, worker_id: str) -> OutboxTask | None:
    row = (
        await session.execute(
            select(OutboxTask)
            .where(OutboxTask.status == "pending", OutboxTask.next_retry_at <= now_utc())
            .order_by(OutboxTask.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    row.status = "running"
    row.locked_by = worker_id
    row.locked_at = now_utc()
    await session.commit()
    return row


async def process_one(sm: async_sessionmaker[AsyncSession], worker_id: str = "worker-0") -> bool:
    """领取并执行一个任务。返回是否有任务被处理。"""
    async with sm() as session:
        task = await _claim_one(session, worker_id)
    if task is None:
        return False

    error: str | None = None
    try:
        handler = _registry.get(task.type)
        if handler is None:
            raise RuntimeError(f"no handler for outbox task type: {task.type}")
        async with sm() as session:
            await handler(session, task)
            await session.commit()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        logger.exception("outbox_task_failed", task_id=task.id, task_type=task.type)

    async with sm() as session:
        if error is None:
            await session.execute(
                update(OutboxTask).where(OutboxTask.id == task.id).values(status="done")
            )
        else:
            retries = task.retries + 1
            if retries > MAX_RETRIES:
                logger.error("outbox_task_dead", task_id=task.id, task_type=task.type, error=error)
                OUTBOX_DEAD_TOTAL.labels(task_type=task.type).inc()
                await session.execute(
                    update(OutboxTask)
                    .where(OutboxTask.id == task.id)
                    .values(status="dead", retries=retries, last_error=error)
                )
            else:
                backoff = timedelta(seconds=BACKOFF_BASE_SECONDS * 2 ** (retries - 1))
                await session.execute(
                    update(OutboxTask)
                    .where(OutboxTask.id == task.id)
                    .values(
                        status="pending",
                        retries=retries,
                        next_retry_at=now_utc() + backoff,
                        last_error=error,
                    )
                )
        await session.commit()
    return True


async def drain(sm: async_sessionmaker[AsyncSession], *, limit: int = 100) -> int:
    """连续处理直到队列空(或到 limit)。测试与关停前冲刷用。"""
    n = 0
    while n < limit and await process_one(sm):
        n += 1
    return n


async def reap_stuck_running(sm: async_sessionmaker[AsyncSession]) -> int:
    """把超时的 running 任务打回 pending(worker 崩溃遗留)。定时任务调用。"""
    async with sm() as session:
        result = cast(
            CursorResult[Any],
            await session.execute(
                update(OutboxTask)
                .where(
                    OutboxTask.status == "running",
                    OutboxTask.locked_at < now_utc() - RUNNING_TIMEOUT,
                )
                .values(status="pending", locked_by=None, locked_at=None)
            ),
        )
        await session.commit()
        if result.rowcount:
            logger.warning("outbox_reaped_stuck_tasks", count=result.rowcount)
        return result.rowcount
