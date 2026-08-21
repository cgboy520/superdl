"""数据盘:CRUD/挂载生命周期/日结幂等/欠费链路。验收:跨实例挂载,释放实例盘保留。"""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app.core.k8s import set_orchestrator
from app.core.k8s.fake import FakeOrchestrator
from app.core.outbox import drain
from app.core.timeutil import now_utc
from app.modules.billing import wallet
from app.modules.billing.models import BalanceLedger, BillDailyDisk
from app.modules.billing.patrol import balance_patrol
from app.modules.billing.settlement import settle_daily_disks
from app.modules.orchestrator.models import DataDisk
from app.modules.orchestrator.reconciler import reconcile_once
from tests.helpers import create_test_sku, create_user_with_key, fund_wallet

pytestmark = pytest.mark.usefixtures("fake")


@pytest.fixture
def fake():
    orch = FakeOrchestrator(auto_ready=False)
    set_orchestrator(orch)
    yield orch
    set_orchestrator(None)


async def create_disk(client, headers, name="data-1", size_gb=100) -> dict:
    resp = await client.post(
        "/api/v1/disks", json={"name": name, "size_gb": size_gb}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestDiskCrud:
    async def test_create_list_expand_delete(self, client, sm, fake):
        headers, user_id, _key = await create_user_with_key(client)
        await fund_wallet(sm, user_id)
        disk = await create_disk(client, headers)
        assert disk["status"] == "active"
        assert disk["price_gb_month"] == "0.0350"

        disks = (await client.get("/api/v1/disks", headers=headers)).json()
        assert len(disks) == 1

        resp = await client.patch(
            f"/api/v1/disks/{disk['uuid']}", json={"size_gb": 200}, headers=headers
        )
        assert resp.json()["size_gb"] == 200

        resp = await client.patch(
            f"/api/v1/disks/{disk['uuid']}", json={"size_gb": 100}, headers=headers
        )
        assert resp.json()["code"] == "DISK_SHRINK_FORBIDDEN"

        resp = await client.delete(f"/api/v1/disks/{disk['uuid']}", headers=headers)
        assert resp.json()["status"] == "deleting"
        await drain(sm)
        disks = (await client.get("/api/v1/disks", headers=headers)).json()
        assert disks == []

    async def test_create_requires_balance(self, client, sm, fake):
        headers, _user_id, _key = await create_user_with_key(client, "13500000001")
        resp = await client.post(
            "/api/v1/disks", json={"name": "d", "size_gb": 100}, headers=headers
        )
        assert resp.json()["code"] == "INSUFFICIENT_BALANCE"

    async def test_size_limits(self, client, sm, fake):
        headers, user_id, _key = await create_user_with_key(client, "13500000002")
        await fund_wallet(sm, user_id)
        resp = await client.post("/api/v1/disks", json={"name": "d", "size_gb": 5}, headers=headers)
        assert resp.json()["code"] == "VALIDATION_ERROR"


class TestMountLifecycle:
    async def test_cross_instance_mount(self, client, sm, fake):
        """验收:A 挂载 → A 释放(盘保留)→ B 挂载同一块盘。"""
        from tests.test_orchestrator_lifecycle import get_instance

        headers, user_id, key_id = await create_user_with_key(client, "13500000010")
        await fund_wallet(sm, user_id, "500.00")
        sku_id = await create_test_sku(sm)
        disk = await create_disk(client, headers)

        # 挂到实例 A
        resp = await client.post(
            "/api/v1/instances",
            json={
                "sku_id": sku_id,
                "image_ref": "img",
                "ssh_key_ids": [key_id],
                "data_disk_id": disk["id"],
            },
            headers=headers,
        )
        assert resp.status_code == 202, resp.text
        a_uuid = resp.json()["uuid"]
        await drain(sm)
        # Pod spec 带 JuiceFS 子路径(挂 /root/data)
        pod = fake.pods[(f"tenant-{user_id}", a_uuid)]
        assert pod.spec.data_disk_subpath == f"disk-{disk['id']}"

        d = (await client.get("/api/v1/disks", headers=headers)).json()[0]
        assert d["mounted_instance_id"] is not None

        # 挂载中不可删
        resp = await client.delete(f"/api/v1/disks/{disk['uuid']}", headers=headers)
        assert resp.json()["code"] == "DISK_IN_USE"

        # 挂载中不可被第二实例占用
        resp = await client.post(
            "/api/v1/instances",
            json={
                "sku_id": sku_id,
                "image_ref": "img",
                "ssh_key_ids": [key_id],
                "data_disk_id": disk["id"],
            },
            headers=headers,
        )
        assert resp.json()["code"] == "DISK_IN_USE"

        # A:running → stop → release → released(盘保留且解挂)
        fake.mark_ready(f"tenant-{user_id}", a_uuid)
        await reconcile_once(sm)
        await client.post(f"/api/v1/instances/{a_uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        await client.delete(f"/api/v1/instances/{a_uuid}", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        assert (await get_instance(client, headers, a_uuid))["status"] == "released"

        d = (await client.get("/api/v1/disks", headers=headers)).json()[0]
        assert d["status"] == "active"
        assert d["mounted_instance_id"] is None

        # B 挂载同一块盘
        resp = await client.post(
            "/api/v1/instances",
            json={
                "sku_id": sku_id,
                "image_ref": "img",
                "ssh_key_ids": [key_id],
                "data_disk_id": disk["id"],
            },
            headers=headers,
        )
        assert resp.status_code == 202, resp.text


class TestDailyDiskBilling:
    async def test_daily_settlement_idempotent(self, client, sm, fake):
        headers, user_id, _key = await create_user_with_key(client, "13500000020")
        await fund_wallet(sm, user_id)
        await create_disk(client, headers, size_gb=100)
        # 把盘的创建时间拨到昨天之前,进入昨日账期
        async with sm() as session:
            await session.execute(update(DataDisk).values(created_at=now_utc() - timedelta(days=2)))
            await session.commit()

        assert await settle_daily_disks(sm) == 1
        assert await settle_daily_disks(sm) == 0  # 幂等:零重复扣款
        async with sm() as session:
            bill = (await session.execute(select(BillDailyDisk))).scalar_one()
            entries = (await session.execute(select(BalanceLedger))).scalars().all()
        # 0.035 × 100 / 30 = 0.11666 → 0.12
        assert bill.amount == Decimal("0.12")
        assert len([e for e in entries if e.type == "consume"]) == 1

        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "99.88"

    async def test_new_disk_not_billed_for_yesterday(self, client, sm, fake):
        headers, user_id, _key = await create_user_with_key(client, "13500000021")
        await fund_wallet(sm, user_id)
        await create_disk(client, headers)  # 今天建的盘
        assert await settle_daily_disks(sm) == 0

    async def test_delete_same_day_pays_final_day(self, client, sm, fake):
        """当日建、当日删:必须出末日账,否则可循环零费用占用存储。"""
        headers, user_id, _key = await create_user_with_key(client, "13500000022")
        await fund_wallet(sm, user_id)
        disk = await create_disk(client, headers, size_gb=100)
        resp = await client.delete(f"/api/v1/disks/{disk['uuid']}", headers=headers)
        assert resp.status_code in (200, 202, 204), resp.text
        async with sm() as session:
            bill = (await session.execute(select(BillDailyDisk))).scalar_one()
        assert bill.amount == Decimal("0.12")
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == "99.88"

    async def test_expand_settles_old_size_first(self, client, sm, fake):
        """扩容前按旧容量结清未出账日期:新容量不追溯到旧日期(多扣用户)。"""
        headers, user_id, _key = await create_user_with_key(client, "13500000023")
        await fund_wallet(sm, user_id)
        disk = await create_disk(client, headers, size_gb=100)
        resp = await client.patch(
            f"/api/v1/disks/{disk['uuid']}", json={"size_gb": 1000}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        async with sm() as session:
            bill = (await session.execute(select(BillDailyDisk))).scalar_one()
        assert bill.size_gb == 100  # 当日按旧容量
        assert bill.amount == Decimal("0.12")

    async def test_frozen_disk_delete_not_billed(self, client, sm, fake):
        """冻结态不计费:欠费回收删盘不补账(否则把有意不计费的日子补回来)。"""
        headers, user_id, _key = await create_user_with_key(client, "13500000024")
        await fund_wallet(sm, user_id)
        disk = await create_disk(client, headers)
        async with sm() as session:
            await session.execute(
                update(DataDisk).where(DataDisk.uuid == disk["uuid"]).values(status="frozen")
            )
            await session.commit()
        await client.delete(f"/api/v1/disks/{disk['uuid']}", headers=headers)
        async with sm() as session:
            assert (await session.execute(select(BillDailyDisk))).scalar_one_or_none() is None


class TestDiskArrearsChain:
    async def test_grace_frozen_wipe_and_recovery(self, client, sm, fake):
        headers, user_id, _key = await create_user_with_key(client, "13500000030")
        await fund_wallet(sm, user_id)
        await create_disk(client, headers)
        # 清空余额 → grace
        async with sm() as session:
            balance = await wallet.get_balance(session, user_id)
            await wallet.debit(session, user_id, balance, type_="adjust", remark="drain")
            await session.commit()
        counts = await balance_patrol(sm)
        assert counts["disks"] == 1
        d = (await client.get("/api/v1/disks", headers=headers)).json()[0]
        assert d["status"] == "grace"
        assert d["grace_started_at"] is not None

        # 宽限超 7 天 → frozen
        async with sm() as session:
            await session.execute(
                update(DataDisk).values(grace_started_at=now_utc() - timedelta(days=8))
            )
            await session.commit()
        await balance_patrol(sm)
        d = (await client.get("/api/v1/disks", headers=headers)).json()[0]
        assert d["status"] == "frozen"

        # 冻结超 30 天 → 清除
        async with sm() as session:
            await session.execute(
                update(DataDisk).values(frozen_started_at=now_utc() - timedelta(days=31))
            )
            await session.commit()
        await balance_patrol(sm)
        await drain(sm)
        disks = (await client.get("/api/v1/disks", headers=headers)).json()
        assert disks == []

    async def test_recharge_restores_disk(self, client, sm, fake):
        headers, user_id, _key = await create_user_with_key(client, "13500000031")
        await fund_wallet(sm, user_id)
        await create_disk(client, headers)
        async with sm() as session:
            balance = await wallet.get_balance(session, user_id)
            await wallet.debit(session, user_id, balance, type_="adjust", remark="drain")
            await session.commit()
        await balance_patrol(sm)
        d = (await client.get("/api/v1/disks", headers=headers)).json()[0]
        assert d["status"] == "grace"

        async with sm() as session:
            await wallet.credit(session, user_id, Decimal("10.00"), type_="recharge")
            await session.commit()
        await balance_patrol(sm)
        d = (await client.get("/api/v1/disks", headers=headers)).json()[0]
        assert d["status"] == "active"
        assert d["grace_started_at"] is None
