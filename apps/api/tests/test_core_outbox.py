from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import outbox
from app.core.outbox import OutboxTask, drain, enqueue, process_one, reap_stuck_running
from app.core.timeutil import now_utc


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


async def test_process_failure_retries_then_dead(sm: async_sessionmaker[AsyncSession], monkeypatch):
    async def bad_handler(_session: AsyncSession, _task: OutboxTask) -> None:
        raise RuntimeError("boom")

    monkeypatch.setitem(outbox._registry, "t_bad", bad_handler)
    # 退避清零,便于连续处理(重试预算现在是 RetryPolicy,不再是模块常量)
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


async def test_unknown_type_goes_dead_not_crash(sm: async_sessionmaker[AsyncSession], monkeypatch):
    monkeypatch.setattr(outbox, "DEFAULT_RETRY_POLICY", outbox.RetryPolicy(backoff_base_seconds=0))
    async with sm() as session:
        enqueue(session, "t_unknown", {})
        await session.commit()
    await drain(sm)
    async with sm() as session:
        task = (await session.execute(select(OutboxTask))).scalar_one()
        assert task.status == "dead"


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
