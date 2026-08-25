"""worker 组件划分(P1-18)的结构性护栏:

- 分片完备性:每个 outbox handler 与定时任务必须恰好归属一个组件。
  漏登记的任务在生产没有任何 Deployment 领取/执行,静默停摆——本文件是防漂移锚点。
- 领取过滤:组件进程在查询层就看不到其它组件的任务(不阻塞、不误领)。
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.workers.components import (
    COMPONENT_OUTBOX_TYPES,
    COMPONENT_SCHEDULED_JOBS,
    WorkerComponent,
    current_component,
    outbox_types_for,
)

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
        """_registry 全量 == 各组件并集:新增 handler 不登记即红(生产静默停摆的前兆)。"""
        from app.core.outbox import _registry
        from app.wiring import wire_modules

        wire_modules()
        registered = set(_registry)
        sharded = set().union(*(COMPONENT_OUTBOX_TYPES[c] for c in _SHARDED))
        assert registered == sharded, (
            f"未登记组件: {sorted(registered - sharded)};登记了但不存在的类型: "
            f"{sorted(sharded - registered)}"
        )

    async def test_scheduled_jobs_disjoint_and_match_scheduler(self, pg_url):
        """定时任务分片:各组件互不重叠,且并集 == register_scheduled_jobs 实际注册的 id 集。
        新增任务未登记组件 / 登记了不存在的任务 / 误删注册——两向漂移都红
        (未登记的任务在生产没有任何 Deployment 执行,静默停摆)。
        pg_url:register_scheduled_jobs 会创建 sessionmaker/engine,须先指向测试库。"""
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        from app.workers.main import register_scheduled_jobs

        seen: dict[str, WorkerComponent] = {}
        for component in _SHARDED:
            for job_id in COMPONENT_SCHEDULED_JOBS[component]:
                assert job_id not in seen, f"{job_id} 同时归属 {seen[job_id]} 与 {component}"
                seen[job_id] = component
        scheduler = AsyncIOScheduler(timezone="UTC")
        register_scheduled_jobs(scheduler)
        scheduler.start(paused=True)  # paused:只取注册清单,不触发任何任务执行
        try:
            registered = {job.id for job in scheduler.get_jobs()}
        finally:
            scheduler.shutdown(wait=False)
        assert registered == set(seen), (
            f"未登记组件: {sorted(registered - seen.keys())};"
            f"登记了但未注册: {sorted(seen.keys() - registered)}"
        )


class TestComponentEnv:
    def test_invalid_component_fails_closed(self, monkeypatch):
        monkeypatch.setenv("SUPERDL_WORKER_COMPONENT", "typo-worker")
        with pytest.raises(RuntimeError, match="SUPERDL_WORKER_COMPONENT"):
            current_component()


class TestClaimFilter:
    async def test_component_only_claims_own_types(self, sm: async_sessionmaker[AsyncSession]):
        """两个类型的任务在队列里:node-mgr 一个也领不到;tenant-mgr 领走 instance.create
        后 notify.sms 仍留在队列(core 的活,不被排序阻塞也不被误领)。"""
        from sqlalchemy import select

        from app.core.outbox import OutboxTask, enqueue, process_one
        from app.wiring import wire_modules

        wire_modules()
        async with sm() as session:
            enqueue(session, "instance.create", {"instance_id": 99999999})
            enqueue(session, "notify.sms", {"phone": "13800000096", "title": "x"})
            await session.commit()

        # node-mgr 的类型集合对这两个任务都不可见
        assert await process_one(sm, "w-node", outbox_types_for(WorkerComponent.NODE_MGR)) is False
        # tenant-mgr 领走 instance.create(实例不存在,handler 幂等消化为 done)
        assert (
            await process_one(sm, "w-tenant", outbox_types_for(WorkerComponent.TENANT_MGR)) is True
        )
        # 再次领取:notify.sms 对 tenant-mgr 不可见,不会被误领
        assert (
            await process_one(sm, "w-tenant", outbox_types_for(WorkerComponent.TENANT_MGR)) is False
        )
        # core 领走 notify.sms
        assert await process_one(sm, "w-core", outbox_types_for(WorkerComponent.CORE)) is True
        async with sm() as session:
            rows = (await session.execute(select(OutboxTask))).scalars().all()
            assert {r.type for r in rows} == {"instance.create", "notify.sms"}
            assert all(r.status == "done" for r in rows)


class TestScheduledJobFilter:
    async def test_component_registers_only_own_jobs(self, monkeypatch, pg_url):
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        from app.workers.main import register_scheduled_jobs

        monkeypatch.setenv("SUPERDL_WORKER_COMPONENT", "prewarm")
        scheduler = AsyncIOScheduler(timezone="UTC")
        register_scheduled_jobs(scheduler)
        scheduler.start(paused=True)
        try:
            ids = {job.id for job in scheduler.get_jobs()}
        finally:
            scheduler.shutdown(wait=False)
        assert ids == {"prewarm_patrol"}
