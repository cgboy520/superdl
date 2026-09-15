"""worker 组件划分:每个 outbox handler 与定时任务恰好归属一个组件;组件进程只领自己的任务。"""

# pyright: reportPrivateUsage=false

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.workers.components import (
    COMPONENT_OUTBOX_TYPES,
    WorkerComponent,
    current_component,
    outbox_types_for,
)
from app.workers.jobs import JOBS, scheduled_jobs_for

_SHARDED = [c for c in WorkerComponent if c is not WorkerComponent.ALL]


class TestPartition:
    def test_outbox_types_disjoint(self):
        seen: dict[str, WorkerComponent] = {}
        for component in _SHARDED:
            for task_type in COMPONENT_OUTBOX_TYPES[component]:
                assert task_type not in seen, (
                    f"{task_type} 同时归属 {seen[task_type]} 与 {component}"
                )
                seen[task_type] = component

    def test_outbox_types_cover_all_registered_handlers(self):
        """_registry 全量 == 各组件并集。"""
        from app.core.outbox import _registry
        from app.wiring import wire_modules

        wire_modules()
        registered = set(_registry)
        sharded = set().union(*(COMPONENT_OUTBOX_TYPES[c] for c in _SHARDED))
        assert registered == sharded, (
            f"未登记组件: {sorted(registered - sharded)};登记了但不存在的类型: "
            f"{sorted(sharded - registered)}"
        )

    async def test_scheduled_jobs_table_matches_scheduler(self, pg_url):
        """各组件任务互斥且完整覆盖 JOBS,调度器注册的 id 集与 JOBS 一致。"""
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        from app.workers.main import register_scheduled_jobs

        assert all(job.component is not WorkerComponent.ALL for job in JOBS)
        by_component = [{j.id for j in scheduled_jobs_for(c)} for c in _SHARDED]
        assert sum(len(s) for s in by_component) == len(JOBS)
        assert set().union(*by_component) == {j.id for j in JOBS}
        scheduler = AsyncIOScheduler(timezone="UTC")
        register_scheduled_jobs(scheduler)
        scheduler.start(paused=True)
        try:
            registered = {job.id for job in scheduler.get_jobs()}
        finally:
            scheduler.shutdown(wait=False)
        assert registered == {j.id for j in JOBS}


class TestComponentEnv:
    def test_invalid_component_fails_closed(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "worker_component", "typo-worker")
        with pytest.raises(RuntimeError, match="SUPERDL_WORKER_COMPONENT"):
            current_component()


class TestClaimFilter:
    async def test_component_only_claims_own_types(self, sm: async_sessionmaker[AsyncSession]):
        """node-mgr 一个也领不到;tenant-mgr 领走 instance.create,notify.sms 留给 core。"""
        from sqlalchemy import select

        from app.core.outbox import OutboxTask, enqueue, process_one
        from app.wiring import wire_modules

        wire_modules()
        async with sm() as session:
            enqueue(session, "instance.create", {"instance_id": 99999999})
            enqueue(session, "notify.sms", {"phone": "13800000096", "title": "x"})
            await session.commit()

        assert await process_one(sm, "w-node", outbox_types_for(WorkerComponent.NODE_MGR)) is False
        assert (
            await process_one(sm, "w-tenant", outbox_types_for(WorkerComponent.TENANT_MGR)) is True
        )
        assert (
            await process_one(sm, "w-tenant", outbox_types_for(WorkerComponent.TENANT_MGR)) is False
        )
        assert await process_one(sm, "w-core", outbox_types_for(WorkerComponent.CORE)) is True
        async with sm() as session:
            rows = (await session.execute(select(OutboxTask))).scalars().all()
            assert {r.type for r in rows} == {"instance.create", "notify.sms"}
            assert all(r.status == "done" for r in rows)


class TestScheduledJobFilter:
    async def test_component_registers_only_own_jobs(self, monkeypatch, pg_url):
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        from app.workers.main import register_scheduled_jobs

        monkeypatch.setattr(get_settings(), "worker_component", "prewarm")
        scheduler = AsyncIOScheduler(timezone="UTC")
        register_scheduled_jobs(scheduler)
        scheduler.start(paused=True)
        try:
            ids = {job.id for job in scheduler.get_jobs()}
        finally:
            scheduler.shutdown(wait=False)
        assert ids == {"prewarm_patrol"}
