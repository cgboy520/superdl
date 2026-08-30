"""数据盘 JuiceFS 目录配额:创建/扩容下发、失败自愈与死信重派、删盘摘除。"""

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.core.outbox import OutboxTask
from app.core.timeutil import now_utc
from tests.helpers import create_disk, create_user_with_key, drain, fund_wallet

pytestmark = pytest.mark.usefixtures("fake")


class TestQuotaDispatch:
    async def test_create_dispatches_quota(self, client: AsyncClient, sm, fake):
        headers, user_id, _key = await create_user_with_key(client)
        await fund_wallet(sm, user_id)
        disk = await create_disk(client, headers, size_gb=100)
        await drain(sm)
        from app.modules.orchestrator.models import DataDisk

        async with sm() as session:
            row = (
                await session.execute(select(DataDisk).where(DataDisk.uuid == disk["uuid"]))
            ).scalar_one()
            assert row.quota_synced is True
            assert fake.disk_quotas[(f"tenant-{user_id}", row.juicefs_subpath)] == 100

    async def test_expand_redispatches_new_capacity(self, client: AsyncClient, sm, fake):
        headers, user_id, _key = await create_user_with_key(client)
        await fund_wallet(sm, user_id)
        disk = await create_disk(client, headers, size_gb=100)
        await drain(sm)
        resp = await client.patch(
            f"/api/v1/disks/{disk['uuid']}", json={"size_gb": 200}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["quota_synced"] is False  # 重下发前立即回落
        await drain(sm)
        from app.modules.orchestrator.models import DataDisk

        async with sm() as session:
            row = (
                await session.execute(select(DataDisk).where(DataDisk.uuid == disk["uuid"]))
            ).scalar_one()
            assert row.quota_synced is True
            assert fake.disk_quotas[(f"tenant-{user_id}", row.juicefs_subpath)] == 200


class TestQuotaFailureAndReconcile:
    async def test_failure_keeps_unsynced_then_retry_recovers(self, client: AsyncClient, sm, fake):
        headers, user_id, _key = await create_user_with_key(client)
        await fund_wallet(sm, user_id)
        fake.fail_next_quota = True
        disk = await create_disk(client, headers, size_gb=100)
        await drain(sm)
        from app.modules.orchestrator.models import DataDisk

        async with sm() as session:
            row = (
                await session.execute(select(DataDisk).where(DataDisk.uuid == disk["uuid"]))
            ).scalar_one()
            assert row.quota_synced is False
            assert fake.disk_quotas == {}
            # 退避中的重试任务回拨到期后再冲刷:恢复下发
            await session.execute(
                update(OutboxTask)
                .where(OutboxTask.type == "disk.quota")
                .values(next_retry_at=now_utc() - timedelta(seconds=1))
            )
            await session.commit()
        await drain(sm)
        async with sm() as session:
            row = (
                await session.execute(select(DataDisk).where(DataDisk.uuid == disk["uuid"]))
            ).scalar_one()
            assert row.quota_synced is True
            assert fake.disk_quotas[(f"tenant-{user_id}", row.juicefs_subpath)] == 100

    async def test_discarded_dead_letter_not_revived(self, client: AsyncClient, sm, fake):
        """管理端人工 discarded 的 disk.quota 不会被对账环复活:reconciler 只重派 dead,
        不按 quota_synced=false 补发(挂了 = 人工忽略失效,死信每轮被重新入队)。"""
        from app.modules.orchestrator.reconciler import reconcile_once

        headers, user_id, _key = await create_user_with_key(client)
        await fund_wallet(sm, user_id)
        await create_disk(client, headers, size_gb=100)  # 配额任务在途,盘 quota_synced=false
        async with sm() as session:
            await session.execute(
                update(OutboxTask)
                .where(OutboxTask.type == "disk.quota")
                .values(status="discarded", updated_at=now_utc() - timedelta(hours=2))
            )
            await session.commit()
        counts = await reconcile_once(sm)
        assert counts["quota_redriven"] == 0
        async with sm() as session:
            statuses = (
                (
                    await session.execute(
                        select(OutboxTask.status).where(OutboxTask.type == "disk.quota")
                    )
                )
                .scalars()
                .all()
            )
        assert statuses == ["discarded"]  # 未补发新任务
        assert fake.disk_quotas == {}

    async def test_reconciler_redrives_dead_quota(self, client: AsyncClient, sm, fake):
        """死信超 1 小时的配额任务被重派(无在途同盘任务时补发一条)。"""
        from app.modules.orchestrator.models import DataDisk
        from app.modules.orchestrator.reconciler import reconcile_once

        headers, user_id, _key = await create_user_with_key(client)
        await fund_wallet(sm, user_id)
        disk = await create_disk(client, headers, size_gb=100)
        async with sm() as session:
            row = (
                await session.execute(select(DataDisk).where(DataDisk.uuid == disk["uuid"]))
            ).scalar_one()
            await session.execute(
                update(OutboxTask)
                .where(OutboxTask.type == "disk.quota")
                .values(
                    status="dead",
                    retries=8,
                    updated_at=now_utc() - timedelta(hours=2),
                )
            )
            await session.execute(
                update(DataDisk).where(DataDisk.id == row.id).values(quota_synced=False)
            )
            await session.commit()
            subpath = row.juicefs_subpath
        counts = await reconcile_once(sm)
        assert counts["quota_redriven"] == 1
        await drain(sm)
        assert fake.disk_quotas[(f"tenant-{user_id}", subpath)] == 100


class TestQuotaDeleteOnWipe:
    async def test_wipe_deletes_quota_first(self, client: AsyncClient, sm, fake):
        headers, user_id, _key = await create_user_with_key(client)
        await fund_wallet(sm, user_id)
        disk = await create_disk(client, headers, size_gb=100)
        await drain(sm)
        from app.modules.orchestrator.models import DataDisk

        async with sm() as session:
            subpath = (
                await session.execute(
                    select(DataDisk.juicefs_subpath).where(DataDisk.uuid == disk["uuid"])
                )
            ).scalar_one()
        assert fake.disk_quotas[(f"tenant-{user_id}", subpath)] == 100
        resp = await client.delete(f"/api/v1/disks/{disk['uuid']}", headers=headers)
        assert resp.status_code == 200, resp.text
        await drain(sm)
        assert (f"tenant-{user_id}", subpath) not in fake.disk_quotas  # 配额已摘除
        assert any(sp == subpath for _ns, sp in fake.wiped_disks)  # 目录已擦除
