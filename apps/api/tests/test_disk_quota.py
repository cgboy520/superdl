"""数据盘 JuiceFS 目录配额(W1-1):创建/扩容下发、失败自愈与对账回填、删盘摘除。"""

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.core.k8s import set_orchestrator
from app.core.k8s.fake import FakeOrchestrator
from app.core.outbox import OutboxTask, drain
from app.core.timeutil import now_utc
from tests.helpers import create_user_with_key, fund_wallet
from tests.test_disks import create_disk

pytestmark = pytest.mark.usefixtures("fake")


@pytest.fixture
def fake():
    orch = FakeOrchestrator(auto_ready=False)
    set_orchestrator(orch)
    yield orch
    set_orchestrator(None)


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
            assert fake.disk_quotas[row.juicefs_subpath] == 100

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
            assert fake.disk_quotas[row.juicefs_subpath] == 200


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
            assert fake.disk_quotas[row.juicefs_subpath] == 100

    async def test_reconciler_backfills_unsynced(self, client: AsyncClient, sm, fake):
        """存量/漏网盘(quota_synced=false 且无在途任务)由对账环补发。"""
        from app.modules.orchestrator.models import DataDisk
        from app.modules.orchestrator.reconciler import reconcile_once

        headers, user_id, _key = await create_user_with_key(client)
        await fund_wallet(sm, user_id)
        disk = await create_disk(client, headers, size_gb=100)
        # 模拟存量盘:清掉在途任务并落 quota_synced=false
        async with sm() as session:
            await session.execute(
                update(OutboxTask).where(OutboxTask.type == "disk.quota").values(status="done")
            )
            await session.execute(
                update(DataDisk).where(DataDisk.uuid == disk["uuid"]).values(quota_synced=False)
            )
            await session.commit()
        counts = await reconcile_once(sm)
        assert counts["quota_enqueued"] == 1
        await drain(sm)
        async with sm() as session:
            row = (
                await session.execute(select(DataDisk).where(DataDisk.uuid == disk["uuid"]))
            ).scalar_one()
            assert row.quota_synced is True

    async def test_reconciler_redrives_dead_quota(self, client: AsyncClient, sm, fake):
        """死信超 1 小时的配额任务被重派(每轮补发上限外的也下轮再来)。"""
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
        assert fake.disk_quotas[subpath] == 100


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
        assert fake.disk_quotas[subpath] == 100
        resp = await client.delete(f"/api/v1/disks/{disk['uuid']}", headers=headers)
        assert resp.status_code == 200, resp.text
        await drain(sm)
        assert subpath not in fake.disk_quotas  # 配额已摘除
        assert any(sp == subpath for _ns, sp in fake.wiped_disks)  # 目录已擦除
