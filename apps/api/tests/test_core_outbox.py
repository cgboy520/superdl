from datetime import timedelta

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import outbox
from app.core.metrics import OUTBOX_PENDING_OLDEST_AGE
from app.core.outbox import (
    OutboxTask,
    enqueue,
    process_one,
    reap_stuck_running,
    report_pending_metrics,
)
from app.core.timeutil import now_utc
from tests.helpers import OutboxDrainError, drain_strict


async def test_enqueue_same_transaction_rollback(sm: async_sessionmaker[AsyncSession]):
    """业务事务回滚时,outbox 任务必须一并消失 —— 事务性 outbox 的根本保证。"""
    async with sm() as session:
        enqueue(session, "noop", {"k": 1})
        await session.rollback()
    async with sm() as session:
        count = len((await session.execute(select(OutboxTask))).scalars().all())
    assert count == 0


async def test_process_success(sm: async_sessionmaker[AsyncSession], monkeypatch):
    calls: list[dict] = []

    async def handler(_session: AsyncSession, task: OutboxTask) -> None:
        calls.append(task.payload)

    monkeypatch.setitem(outbox._registry, "t_ok", handler)

    async with sm() as session:
        enqueue(session, "t_ok", {"x": 42})
        await session.commit()

    assert await process_one(sm) is True
    assert calls == [{"x": 42}]

    async with sm() as session:
        task = (await session.execute(select(OutboxTask))).scalar_one()
        assert task.status == "done"


async def test_enqueue_carries_request_id_into_handler_context(
    sm: async_sessionmaker[AsyncSession], monkeypatch
):
    """跨进程请求链:enqueue 把当前 contextvar 的 request_id 写进 payload
    (_request_id 键);执行时回填日志上下文(handler 内可见),执行完解绑不残留。"""
    import structlog

    seen: list[object] = []

    async def handler(_session: AsyncSession, _task: OutboxTask) -> None:
        seen.append(structlog.contextvars.get_contextvars().get("request_id"))

    monkeypatch.setitem(outbox._registry, "t_rid", handler)
    structlog.contextvars.bind_contextvars(request_id="rid-test-1")
    try:
        async with sm() as session:
            task = enqueue(session, "t_rid", {"x": 1})
            assert task.payload["_request_id"] == "rid-test-1"
            await session.commit()
    finally:
        structlog.contextvars.unbind_contextvars("request_id")

    assert await process_one(sm) is True
    assert seen == ["rid-test-1"]  # handler 执行期回填
    assert structlog.contextvars.get_contextvars().get("request_id") is None  # 执行完已解绑


async def test_enqueue_without_request_id_keeps_payload(sm: async_sessionmaker[AsyncSession]):
    """无 request_id 上下文(如 worker 内部入队):payload 原样,不画蛇添足。"""
    async with sm() as session:
        task = enqueue(session, "noop_plain", {"x": 1})
        assert task.payload == {"x": 1}


async def test_process_failure_retries_then_dead(sm: async_sessionmaker[AsyncSession], monkeypatch):
    async def bad_handler(_session: AsyncSession, _task: OutboxTask) -> None:
        raise RuntimeError("boom")

    monkeypatch.setitem(outbox._registry, "t_bad", bad_handler)
    # 退避清零,便于连续处理
    monkeypatch.setattr(outbox, "DEFAULT_RETRY_POLICY", outbox.RetryPolicy(backoff_base_seconds=0))

    async with sm() as session:
        enqueue(session, "t_bad", {})
        await session.commit()

    # 首次 + MAX_RETRIES 次重试全部失败后进 dead
    for _ in range(outbox.MAX_RETRIES + 1):
        assert await process_one(sm) is True

    async with sm() as session:
        task = (await session.execute(select(OutboxTask))).scalar_one()
        assert task.status == "dead"
        assert task.retries == outbox.MAX_RETRIES + 1
        assert "boom" in (task.last_error or "")

    # dead 任务不再被领取
    assert await process_one(sm) is False


async def test_backoff_schedule(sm: async_sessionmaker[AsyncSession], monkeypatch):
    async def bad_handler(_session: AsyncSession, _task: OutboxTask) -> None:
        raise RuntimeError("boom")

    monkeypatch.setitem(outbox._registry, "t_backoff", bad_handler)

    async with sm() as session:
        enqueue(session, "t_backoff", {})
        await session.commit()

    assert await process_one(sm) is True  # 第一次失败
    async with sm() as session:
        task = (await session.execute(select(OutboxTask))).scalar_one()
        assert task.status == "pending"
        assert task.retries == 1
        assert task.next_retry_at > now_utc()  # 退避生效

    # 未到期 → 不领取
    assert await process_one(sm) is False


async def test_reaper_requeues_stuck_running(sm: async_sessionmaker[AsyncSession]):
    async with sm() as session:
        task = OutboxTask(
            type="t_stuck",
            payload={},
            status="running",
            locked_by="dead-worker",
            locked_at=now_utc() - timedelta(minutes=30),
        )
        session.add(task)
        await session.commit()

    assert await reap_stuck_running(sm) == 1
    async with sm() as session:
        task = (await session.execute(select(OutboxTask))).scalar_one()
        assert task.status == "pending"
        assert task.locked_by is None
        # 复活必须计一次失败并退避:杀进程的任务不计数会被 5 分钟无限重投,永不进 dead
        assert task.retries == 1
        assert task.next_retry_at > now_utc()


async def test_reaper_dead_letter_after_budget_exhausted(sm: async_sessionmaker[AsyncSession]):
    """崩溃循环的任务:重试预算耗尽后必须进 dead(触发告警与管理端可见),而非无限重投。"""
    async with sm() as session:
        task = OutboxTask(
            type="t_stuck",
            payload={},
            status="running",
            retries=outbox.MAX_RETRIES,  # 已达预算上限,再复活即越界
            locked_by="dead-worker",
            locked_at=now_utc() - timedelta(minutes=30),
        )
        session.add(task)
        await session.commit()

    assert await reap_stuck_running(sm) == 1
    async with sm() as session:
        task = (await session.execute(select(OutboxTask))).scalar_one()
        assert task.status == "dead"
        assert task.retries == outbox.MAX_RETRIES + 1
        assert task.last_error is not None and "reaped" in task.last_error


class TestRetryPolicy:
    async def test_per_type_budget_overrides_default(self, sm):
        """「等外部作业完成」型任务(disk.wipe)必须有更长的重试预算,否则盘卡 deleting。"""
        from app.core.outbox import DEFAULT_RETRY_POLICY, retry_policy_for
        from app.modules.orchestrator import handlers as _handlers  # noqa: F401 注册重试预算

        assert retry_policy_for("instance.create") is DEFAULT_RETRY_POLICY
        wipe = retry_policy_for("disk.wipe")
        assert wipe.max_retries > DEFAULT_RETRY_POLICY.max_retries
        # 预算总时长(退避封顶后)必须够擦一块大盘:> 30 分钟
        total = sum(
            min(wipe.backoff_base_seconds * 2**i, wipe.backoff_max_seconds)
            for i in range(wipe.max_retries)
        )
        assert total > 1800


class TestTaskTimeout:
    async def test_hung_handler_is_timed_out_and_retried(self, sm, monkeypatch):
        """队列是全局串行 FIFO 且单副本:一个挂死的调用会把所有人的关机请求排在后面。

        超时把「队头卡死」变成一次可重试的失败,而不是无限期占住队头等 reaper 兜底。
        """
        import asyncio

        from app.core import outbox as outbox_mod

        monkeypatch.setattr(outbox_mod, "TASK_TIMEOUT_SECONDS", 0.05)

        async def _hang(session, task):
            await asyncio.sleep(5)

        # 与同文件其它用例同规:setitem 注入(monkeypatch 收尾回滚);
        # @outbox_handler 会永久写进全局 _registry,污染全量测试会话里其它模块的断言
        monkeypatch.setitem(outbox_mod._registry, "test.hang", _hang)

        async with sm() as session:
            enqueue(session, "test.hang", {})
            await session.commit()
        assert await process_one(sm) is True
        async with sm() as session:
            row = (
                await session.execute(select(OutboxTask).where(OutboxTask.type == "test.hang"))
            ).scalar_one()
        assert row.status == "pending"  # 退避重试,不是 done
        assert row.retries == 1
        assert "TimeoutError" in (row.last_error or "")


class TestClaimOrder:
    async def test_prefers_earliest_next_retry_at(
        self, sm: async_sessionmaker[AsyncSession], monkeypatch
    ):
        """领取按 (next_retry_at, id) 排序:到期最早优先,不是纯 id FIFO。"""
        calls: list[str] = []

        async def handler(_session: AsyncSession, task: OutboxTask) -> None:
            calls.append(task.payload["k"])

        monkeypatch.setitem(outbox._registry, "t_order", handler)

        async with sm() as session:
            # late 先入库(id 更小)但更晚到期;early 应被先领取
            session.add(OutboxTask(type="t_order", payload={"k": "late"}, next_retry_at=now_utc()))
            session.add(
                OutboxTask(
                    type="t_order",
                    payload={"k": "early"},
                    next_retry_at=now_utc() - timedelta(minutes=5),
                )
            )
            await session.commit()

        assert await process_one(sm) is True
        assert calls == ["early"]


class TestConcurrency:
    async def test_concurrent_workers_claim_distinct_tasks(
        self, sm: async_sessionmaker[AsyncSession], monkeypatch
    ):
        """SKIP LOCKED 下多个领取协程并发执行不同任务,消除全局串行 FIFO 的队头阻塞。

        屏障模式:两路 handler 与主协程在 Barrier(3) 汇合后才放行;领取若退化为串行,
        先跑的 handler 永远等不到汇合 → wait_for 超时判负,不靠 wall-clock。
        """
        import asyncio

        gate = asyncio.Barrier(3)
        started: list[str] = []

        async def gated_handler(_session: AsyncSession, task: OutboxTask) -> None:
            started.append(task.payload["k"])
            await gate.wait()  # 两路 handler 同时在场才放行

        monkeypatch.setitem(outbox._registry, "t_gated", gated_handler)

        async with sm() as session:
            enqueue(session, "t_gated", {"k": "a"})
            enqueue(session, "t_gated", {"k": "b"})
            await session.commit()

        # 超时只是「串行退化 → 屏障凑不齐」的判负兜底:Linux CI 毫秒级放行;
        # Windows 本机第二条并发 claim 的 asyncpg 建联可能慢到十几秒,余量放宽
        r0, r1, _ = await asyncio.wait_for(
            asyncio.gather(process_one(sm, "w-0"), process_one(sm, "w-1"), gate.wait()),
            timeout=60,
        )
        assert (r0, r1) == (True, True)
        assert sorted(started) == ["a", "b"]

        async with sm() as session:
            rows = (await session.execute(select(OutboxTask))).scalars().all()
            assert {r.status for r in rows} == {"done"}


class TestDrainStrict:
    async def test_all_done_returns_counts(self, sm: async_sessionmaker[AsyncSession], monkeypatch):
        async def ok_handler(_session: AsyncSession, _task: OutboxTask) -> None:
            return None

        monkeypatch.setitem(outbox._registry, "t_ds_ok", ok_handler)

        async with sm() as session:
            enqueue(session, "t_ds_ok", {"i": 1})
            enqueue(session, "t_ds_ok", {"i": 2})
            await session.commit()

        assert await drain_strict(sm) == (2, 0)

    async def test_failed_task_raises_with_counts(
        self, sm: async_sessionmaker[AsyncSession], monkeypatch
    ):
        """drain 只报处理个数,失败任务静默滑进重试;drain_strict 必须把失败抛出来。"""

        async def bad_handler(_session: AsyncSession, _task: OutboxTask) -> None:
            raise RuntimeError("boom")

        monkeypatch.setitem(outbox._registry, "t_ds_bad", bad_handler)

        async with sm() as session:
            enqueue(session, "t_ds_bad", {})
            await session.commit()

        with pytest.raises(OutboxDrainError) as exc_info:
            await drain_strict(sm)
        assert (exc_info.value.done_count, exc_info.value.failed_count) == (0, 1)

        # 失败任务退避回 pending(未死循环、未误标 done)
        async with sm() as session:
            row = (await session.execute(select(OutboxTask))).scalar_one()
            assert row.status == "pending"
            assert row.retries == 1

    async def test_mixed_outcomes_counts_both(
        self, sm: async_sessionmaker[AsyncSession], monkeypatch
    ):
        async def ok_handler(_session: AsyncSession, _task: OutboxTask) -> None:
            return None

        async def bad_handler(_session: AsyncSession, _task: OutboxTask) -> None:
            raise RuntimeError("boom")

        monkeypatch.setitem(outbox._registry, "t_ds_ok2", ok_handler)
        monkeypatch.setitem(outbox._registry, "t_ds_bad2", bad_handler)

        async with sm() as session:
            enqueue(session, "t_ds_ok2", {})
            enqueue(session, "t_ds_bad2", {})
            await session.commit()

        with pytest.raises(OutboxDrainError) as exc_info:
            await drain_strict(sm)
        assert (exc_info.value.done_count, exc_info.value.failed_count) == (1, 1)


class TestTerminalWriteOwnership:
    async def test_ownership_lost_write_is_dropped(
        self, sm: async_sessionmaker[AsyncSession], monkeypatch
    ):
        """执行期间被 reaper 回收(锁易主)→ 终态写必须放弃,不得覆盖接管者的状态。"""
        from sqlalchemy import update

        async def handler(_session: AsyncSession, task: OutboxTask) -> None:
            # 模拟 reaper 在 handler 执行期回收本任务(独立事务)
            async with sm() as s2:
                await s2.execute(
                    update(OutboxTask)
                    .where(OutboxTask.id == task.id)
                    .values(status="pending", locked_by=None, locked_at=None)
                )
                await s2.commit()

        monkeypatch.setitem(outbox._registry, "t_race", handler)

        async with sm() as session:
            enqueue(session, "t_race", {})
            await session.commit()

        assert await process_one(sm, "w-victim") is True  # handler 本身成功
        async with sm() as session:
            row = (await session.execute(select(OutboxTask))).scalar_one()
            assert row.status == "pending"  # 没被写成 done
            assert row.locked_by is None


def test_running_timeout_is_double_task_timeout():
    """reaper 打回 running 的窗口必须显著大于任务执行上限,否则正常执行中的任务会被双认领。"""
    assert timedelta(seconds=2 * outbox.TASK_TIMEOUT_SECONDS) <= outbox.RUNNING_TIMEOUT


class TestPendingMetrics:
    async def test_tracks_oldest_pending_age(self, sm: async_sessionmaker[AsyncSession]):
        """积压指标 = 最老 pending 任务年龄(挂了 = 消费停滞类静默停摆失去唯一可观测出口)。"""
        async with sm() as session:
            task = enqueue(session, "t_metric", {})
            await session.flush()
            await session.execute(
                update(OutboxTask)
                .where(OutboxTask.id == task.id)
                .values(created_at=now_utc() - timedelta(hours=2))
            )
            await session.commit()
        await report_pending_metrics(sm)
        assert OUTBOX_PENDING_OLDEST_AGE._value.get() >= 7200

    async def test_zero_when_no_pending(self, sm: async_sessionmaker[AsyncSession]):
        """队列排空后指标归零(告警 for 10m 能自动恢复,不残留陈旧值)。"""
        async with sm() as session:
            await session.execute(delete(OutboxTask))
            await session.commit()
        await report_pending_metrics(sm)
        assert OUTBOX_PENDING_OLDEST_AGE._value.get() == 0
