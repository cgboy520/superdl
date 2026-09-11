"""镜像预热:巡检铺行/收敛/复检/清理 + handler 幂等(FakeOrchestrator 全链路)。"""

from datetime import timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.k8s.fake import FakeOrchestrator
from app.core.outbox import OutboxTask
from app.core.timeutil import now_utc
from app.modules.catalog.models import ImageNodeCache, PlatformImage
from app.modules.catalog.prewarm import prewarm_patrol
from tests.helpers import IMAGE_PYTORCH, drain, drain_strict

pytestmark = pytest.mark.usefixtures("fake")

IMAGE_REF = IMAGE_PYTORCH


async def make_image(
    sm: async_sessionmaker[AsyncSession], *, ref: str = IMAGE_REF, enabled: bool = True
) -> int:
    async with sm() as session:
        img = PlatformImage(
            framework="PyTorch",
            framework_version="2.9.0",
            python_version="3.12",
            cuda_version="12.8",
            image_ref=ref,
            prewarm_enabled=enabled,
        )
        session.add(img)
        await session.commit()
        return img.id


async def cache_rows(sm: async_sessionmaker[AsyncSession]) -> list[ImageNodeCache]:
    async with sm() as session:
        return list(
            (
                await session.execute(select(ImageNodeCache).order_by(ImageNodeCache.node_name))
            ).scalars()
        )


async def pending_tasks(sm: async_sessionmaker[AsyncSession]) -> int:
    async with sm() as session:
        rows = (
            await session.execute(
                select(OutboxTask).where(
                    OutboxTask.type == "image.prewarm", OutboxTask.status == "pending"
                )
            )
        ).scalars()
        return len(list(rows))


class TestPrewarmFullChain:
    async def test_cpu_pool_nodes_are_never_prewarmed(self, sm, fake: FakeOrchestrator) -> None:
        """无卡机不铺预热行。"""
        await make_image(sm)
        await prewarm_patrol(sm)
        nodes = {r.node_name for r in await cache_rows(sm)}
        assert any(n.pool_label == "cpu" for n in await fake.list_nodes()), "fake 应有 cpu 节点"
        assert not any(n.startswith("fake-cpu-") for n in nodes), nodes

    async def test_plan_pull_converge_to_cached(self, sm, fake: FakeOrchestrator) -> None:
        """建镜像 → 巡检铺行(=节点数) → drain 置 pulling → 巡检收敛 cached。"""
        await make_image(sm)
        counts = await prewarm_patrol(sm)
        assert counts["planned"] == 3  # kata/hami/mig 三节点
        rows = await cache_rows(sm)
        assert [r.status for r in rows] == ["pending"] * 3
        assert await pending_tasks(sm) == 3

        # handler:建 Job + 行置 pulling
        assert await drain_strict(sm) == (3, 0)
        rows = await cache_rows(sm)
        assert [r.status for r in rows] == ["pulling"] * 3
        assert len(fake.prewarm_jobs) == 3

        counts = await prewarm_patrol(sm)
        assert counts["cached"] == 3
        rows = await cache_rows(sm)
        assert [r.status for r in rows] == ["cached"] * 3
        assert all(r.checked_at is not None for r in rows)
        assert fake.prewarm_jobs == {}  # 收敛后 Job 已清理

    async def test_handler_idempotent_no_duplicate_job(self, sm, fake: FakeOrchestrator) -> None:
        """同一(镜像,节点)任务重复执行:Job 唯一、行不重复。"""
        image_id = await make_image(sm)
        await prewarm_patrol(sm)
        await drain(sm)
        # 重复入队同一目标再 drain
        async with sm() as session:
            from app.core.outbox import enqueue

            enqueue(
                session, "image.prewarm", {"image_id": image_id, "node_name": "fake-kata-node-1"}
            )
            await session.commit()
        await drain(sm)
        assert len(fake.prewarm_jobs) == 3
        assert len(await cache_rows(sm)) == 3


class TestPrewarmFailure:
    async def test_failed_records_error_and_throttled_retry(
        self, sm, fake: FakeOrchestrator
    ) -> None:
        fake.auto_prewarm = False
        await make_image(sm)
        await prewarm_patrol(sm)
        await drain(sm)  # rows → pulling,jobs → running
        fake.set_prewarm_state("fake-kata-node-1", IMAGE_REF, "failed")

        counts = await prewarm_patrol(sm)
        assert counts["failed"] == 1
        rows = await cache_rows(sm)
        failed = [r for r in rows if r.status == "failed"]
        assert len(failed) == 1
        assert "ErrImagePull" in (failed[0].last_error or "")

        # 节流窗口内不自动重试
        counts = await prewarm_patrol(sm)
        assert counts["requeued"] == 0

        # 回拨 updated_at 超过 30min → 自动回 pending 并重新入队
        async with sm() as session:
            await session.execute(
                update(ImageNodeCache)
                .where(ImageNodeCache.id == failed[0].id)
                .values(updated_at=now_utc() - timedelta(minutes=31))
            )
            await session.commit()
        counts = await prewarm_patrol(sm)
        assert counts["requeued"] == 1
        assert await pending_tasks(sm) == 1

    async def test_absent_job_requeued(self, sm, fake: FakeOrchestrator) -> None:
        """Job 被 TTL 清理(pulling 行悬置)→ 巡检回 pending 重派。"""
        fake.auto_prewarm = False
        await make_image(sm)
        await prewarm_patrol(sm)
        await drain(sm)
        fake.prewarm_jobs.clear()  # 模拟 TTL 清理
        counts = await prewarm_patrol(sm)
        assert counts["requeued"] == 3
        rows = await cache_rows(sm)
        assert [r.status for r in rows] == ["pending"] * 3


class TestPrewarmLifecycle:
    async def test_disable_image_removes_rows(self, sm, fake: FakeOrchestrator) -> None:
        image_id = await make_image(sm)
        await prewarm_patrol(sm)
        assert len(await cache_rows(sm)) == 3
        async with sm() as session:
            img = await session.get(PlatformImage, image_id)
            assert img is not None
            img.prewarm_enabled = False
            await session.commit()
        counts = await prewarm_patrol(sm)
        assert counts["removed"] == 3
        assert await cache_rows(sm) == []

    async def test_node_scaling(self, sm, fake: FakeOrchestrator) -> None:
        await make_image(sm)
        await prewarm_patrol(sm)
        assert len(await cache_rows(sm)) == 3
        # 新节点加入(新池)→ 补行;节点消失 → 行删除
        fake.pool_capacity["kata2"] = 8
        counts = await prewarm_patrol(sm)
        assert counts["planned"] == 1
        assert len(await cache_rows(sm)) == 4
        del fake.pool_capacity["kata2"]
        counts = await prewarm_patrol(sm)
        assert counts["removed"] == 1
        assert len(await cache_rows(sm)) == 3

    async def test_recheck_window_requeues_cached(self, sm, fake: FakeOrchestrator) -> None:
        await make_image(sm)
        await prewarm_patrol(sm)
        await drain(sm)
        await prewarm_patrol(sm)  # 全部 cached
        async with sm() as session:
            await session.execute(
                update(ImageNodeCache).values(checked_at=now_utc() - timedelta(hours=25))
            )
            await session.commit()
        counts = await prewarm_patrol(sm)  # 默认复检窗口 24h
        assert counts["requeued"] == 3
        rows = await cache_rows(sm)
        assert [r.status for r in rows] == ["pending"] * 3

    async def test_ref_change_via_sql_invalidates_cached_rows(
        self, sm, fake: FakeOrchestrator
    ) -> None:
        """绕过服务层直接改 image_ref 时,缓存行作废重拉。"""
        image_id = await make_image(sm)
        await prewarm_patrol(sm)
        await drain(sm)
        await prewarm_patrol(sm)  # 全部 cached,cached_ref = 旧 ref
        rows = await cache_rows(sm)
        assert [r.status for r in rows] == ["cached"] * 3
        assert {r.cached_ref for r in rows} == {IMAGE_REF}

        async with sm() as session:
            await session.execute(
                update(PlatformImage)
                .where(PlatformImage.id == image_id)
                .values(image_ref=IMAGE_REF + "@sha256:" + "b" * 64)
            )
            await session.commit()

        counts = await prewarm_patrol(sm)
        assert counts["requeued"] == 3
        rows = await cache_rows(sm)
        assert [r.status for r in rows] == ["pending"] * 3
        assert {r.cached_ref for r in rows} == {None}

    async def test_pending_row_requeued_after_timeout(self, sm, fake: FakeOrchestrator) -> None:
        """pending 行超时(默认 10min)重派。"""
        await make_image(sm)
        await prewarm_patrol(sm)
        assert await pending_tasks(sm) == 3
        # 任务丢失:删掉 pending 任务,行仍 pending
        async with sm() as session:
            for t in (await session.execute(select(OutboxTask))).scalars().all():
                await session.delete(t)
            await session.execute(
                update(ImageNodeCache).values(updated_at=now_utc() - timedelta(minutes=11))
            )
            await session.commit()
        counts = await prewarm_patrol(sm)
        assert counts["requeued"] == 3
        assert await pending_tasks(sm) == 3

    async def test_not_ready_node_gets_no_new_task(self, sm, fake: FakeOrchestrator) -> None:
        """NotReady 节点保留行但不派新任务;failed 重试与 pending 重派同样跳过。"""
        from app.core.k8s.base import NodeInfo

        fake.inject_node(
            NodeInfo(
                name="sick-node",
                pool_label="hami",
                gpu_model_label="RTX4090",
                gpu_total=8,
                gpu_used=0,
                status="NotReady",
            )
        )
        image_id = await make_image(sm)
        counts = await prewarm_patrol(sm)
        assert counts["planned"] == 3  # 只铺 Ready 三节点,sick-node 不在期望集
        # sick-node 的 failed 行:重试不派发
        async with sm() as session:
            session.add(
                ImageNodeCache(
                    image_id=image_id,
                    node_name="sick-node",
                    status="failed",
                    updated_at=now_utc() - timedelta(hours=1),
                )
            )
            await session.commit()
        counts = await prewarm_patrol(sm)
        assert counts["requeued"] == 0
        async with sm() as session:
            tasks = (
                (
                    await session.execute(
                        select(OutboxTask).where(OutboxTask.type == "image.prewarm")
                    )
                )
                .scalars()
                .all()
            )
        assert all(t.payload["node_name"] != "sick-node" for t in tasks)


class TestPrewarmPullSecret:
    async def test_job_references_managed_secret_only_when_robot_configured(
        self, sm, fake: FakeOrchestrator
    ) -> None:
        """预热 Job 与实例 Pod 同一条凭据链:配了机器人才托管 Secret 并引用。"""
        from app.core.config import get_settings
        from app.core.platform_config import set_platform_settings
        from app.core.registry import PULL_SECRET_NAME

        await make_image(sm)
        await prewarm_patrol(sm)
        await drain(sm)
        assert set(fake.prewarm_pull_secrets.values()) == {None}
        assert fake.pull_secrets == {}

        async with sm() as session:
            await set_platform_settings(
                session,
                {
                    "registry_host": "harbor.example.com",
                    "registry_robot_name": "robot$superdl+pull",
                    "registry_robot_secret": "s3cret",
                },
                updated_by=None,
            )
            await session.commit()
        image_id = await make_image(sm, ref="harbor.example.com/superdl/tensorflow:2.20-cu128")
        await prewarm_patrol(sm)
        await drain(sm)
        ref = "harbor.example.com/superdl/tensorflow:2.20-cu128"
        used = {v for (node, r), v in fake.prewarm_pull_secrets.items() if r == ref}
        assert used == {PULL_SECRET_NAME}, (image_id, fake.prewarm_pull_secrets)
        assert get_settings().k8s_platform_namespace in fake.pull_secrets
