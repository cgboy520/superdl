"""数据盘 PVC 的创建、扩容与失败重派。"""

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.core.k8s.base import data_disk_pvc_name
from app.core.outbox import OutboxTask
from app.core.timeutil import now_utc
from tests.helpers import create_disk, create_user_with_key, drain, fund_wallet

pytestmark = pytest.mark.usefixtures("fake")


class TestProvisionDispatch:
    async def test_expand_redispatches_new_capacity(self, client: AsyncClient, sm, fake):
        headers, user_id, _key = await create_user_with_key(client)
        await fund_wallet(sm, user_id)
        disk = await create_disk(client, headers, size_gb=100)
        await drain(sm)
        resp = await client.patch(
            f"/api/v1/disks/{disk['uuid']}", json={"size_gb": 200}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["provisioned"] is False
        await drain(sm)
        from app.modules.orchestrator.models import DataDisk

        async with sm() as session:
            row = (
                await session.execute(select(DataDisk).where(DataDisk.uuid == disk["uuid"]))
            ).scalar_one()
            assert row.provisioned is True
        assert fake.data_disks[(f"tenant-{user_id}", data_disk_pvc_name(disk["uuid"]))] == 200


class TestProvisionFailureAndReconcile:
    async def test_failure_keeps_unprovisioned_then_retry_recovers(
        self, client: AsyncClient, sm, fake
    ):
        headers, user_id, _key = await create_user_with_key(client)
        await fund_wallet(sm, user_id)
        fake.fail_next_disk = True
        disk = await create_disk(client, headers, size_gb=100)
        await drain(sm)
        from app.modules.orchestrator.models import DataDisk

        async with sm() as session:
            row = (
                await session.execute(select(DataDisk).where(DataDisk.uuid == disk["uuid"]))
            ).scalar_one()
            assert row.provisioned is False
            assert fake.data_disks == {}
            await session.execute(
                update(OutboxTask)
                .where(OutboxTask.type == "disk.provision")
                .values(next_retry_at=now_utc() - timedelta(seconds=1))
            )
            await session.commit()
        await drain(sm)
        async with sm() as session:
            row = (
                await session.execute(select(DataDisk).where(DataDisk.uuid == disk["uuid"]))
            ).scalar_one()
            assert row.provisioned is True
        assert fake.data_disks[(f"tenant-{user_id}", data_disk_pvc_name(disk["uuid"]))] == 100

    async def test_discarded_dead_letter_not_revived(self, client: AsyncClient, sm, fake):
        """人工 discarded 的 disk.provision 不被对账环复活:reconciler 只重派 dead。"""
        from app.modules.orchestrator.reconciler import reconcile_once

        headers, user_id, _key = await create_user_with_key(client)
        await fund_wallet(sm, user_id)
        await create_disk(client, headers, size_gb=100)
        async with sm() as session:
            await session.execute(
                update(OutboxTask)
                .where(OutboxTask.type == "disk.provision")
                .values(status="discarded", updated_at=now_utc() - timedelta(hours=2))
            )
            await session.commit()
        counts = await reconcile_once(sm)
        assert counts["provision_redriven"] == 0
        async with sm() as session:
            statuses = (
                (
                    await session.execute(
                        select(OutboxTask.status).where(OutboxTask.type == "disk.provision")
                    )
                )
                .scalars()
                .all()
            )
        assert statuses == ["discarded"]
        assert fake.data_disks == {}

    async def test_reconciler_redrives_dead_provision(self, client: AsyncClient, sm, fake):
        """死信超 1 小时的下发任务被重派(无在途同盘任务时补发一条)。"""
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
                .where(OutboxTask.type == "disk.provision")
                .values(status="dead", retries=8, updated_at=now_utc() - timedelta(hours=2))
            )
            await session.execute(
                update(DataDisk).where(DataDisk.id == row.id).values(provisioned=False)
            )
            await session.commit()
        counts = await reconcile_once(sm)
        assert counts["provision_redriven"] == 1
        await drain(sm)
        assert fake.data_disks[(f"tenant-{user_id}", data_disk_pvc_name(disk["uuid"]))] == 100
