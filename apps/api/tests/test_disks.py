"""数据盘 CRUD、跨实例挂载、日结幂等与欠费链路。"""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app.core.money import disk_daily_charge
from app.core.timeutil import BILLING_DAY_OFFSET, billing_day_floor, now_utc
from app.modules.billing import wallet
from app.modules.billing.models import BalanceLedger, BillDailyDisk
from app.modules.billing.patrol import balance_patrol
from app.modules.billing.settlement import settle_daily_disks
from app.modules.orchestrator.models import DataDisk
from app.modules.orchestrator.reconciler import reconcile_once
from tests.helpers import (
    create_disk,
    create_test_sku,
    create_user_with_key,
    drain,
    fund_wallet,
    funded_user,
    get_instance,
    set_platform_setting,
)

pytestmark = pytest.mark.usefixtures("fake")


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
        assert fake.deleted_data_disks[-1] == (f"tenant-{user_id}", f"disk-{disk['uuid']}")

    async def test_create_requires_balance(self, client, sm, fake):
        headers, _user_id, _key = await create_user_with_key(client, "13500000001")
        resp = await client.post(
            "/api/v1/disks", json={"name": "d", "size_gb": 100}, headers=headers
        )
        assert resp.json()["code"] == "INSUFFICIENT_BALANCE"

    async def test_expand_requires_balance(self, client, sm, fake):
        """扩容走与创建同一条燃烧率护栏。"""
        headers, user_id, _key = await create_user_with_key(client, "13500000003")
        await fund_wallet(sm, user_id)
        disk = await create_disk(client, headers, size_gb=100)
        await drain(sm)
        async with sm() as session:
            await wallet.debit(session, user_id, Decimal("99.99"), allow_negative=False)
            await session.commit()
        resp = await client.patch(
            f"/api/v1/disks/{disk['uuid']}", json={"size_gb": 4096}, headers=headers
        )
        assert resp.json()["code"] == "INSUFFICIENT_BALANCE"
        d = (await client.get("/api/v1/disks", headers=headers)).json()[0]
        assert d["size_gb"] == 100

    async def test_size_limits(self, client, sm, fake):
        headers, _user_id, _key = await funded_user(client, sm, "13500000002")
        resp = await client.post("/api/v1/disks", json={"name": "d", "size_gb": 5}, headers=headers)
        assert resp.json()["code"] == "VALIDATION_ERROR"


class TestMountLifecycle:
    async def test_attach_rejected_until_provisioned(self, client, sm, fake):
        """配额未下发成功的盘不得挂载;同步完成后即可挂。"""
        headers, _user_id, key_id = await funded_user(client, sm, "13500000013", "500.00")
        sku_id = await create_test_sku(sm)
        disk = await create_disk(client, headers)
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
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "disks.notProvisioned"
        await drain(sm)
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

    async def test_start_after_delete_disk_detaches(self, client, sm, fake):
        """停机后删盘摘除挂载引用,再次开机不引用该盘 PVC。"""
        headers, user_id, key_id = await funded_user(client, sm, "13500000011", "500.00")
        sku_id = await create_test_sku(sm)
        disk = await create_disk(client, headers)
        await drain(sm)
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
        fake.mark_ready(f"tenant-{user_id}", a_uuid)
        await reconcile_once(sm)
        await client.post(f"/api/v1/instances/{a_uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        assert (await get_instance(client, headers, a_uuid))["status"] == "stopped"
        resp = await client.delete(f"/api/v1/disks/{disk['uuid']}", headers=headers)
        assert resp.status_code == 200, resp.text
        await drain(sm)
        async with sm() as session:
            from app.modules.orchestrator.models import Instance

            inst = (
                await session.execute(select(Instance).where(Instance.uuid == a_uuid))
            ).scalar_one()
            assert inst.data_disk_id is None
        resp = await client.post(f"/api/v1/instances/{a_uuid}/start", headers=headers)
        assert resp.status_code == 200, resp.text
        await drain(sm)
        pod = fake.pods[(f"tenant-{user_id}", a_uuid)]
        assert pod.spec.data_disk_pvc is None

    async def test_start_rejected_when_disk_deleting(self, client, sm, fake):
        """挂载盘处于 deleting 时拒绝开机。"""
        headers, user_id, key_id = await funded_user(client, sm, "13500000012", "500.00")
        sku_id = await create_test_sku(sm)
        disk = await create_disk(client, headers)
        await drain(sm)
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
        fake.mark_ready(f"tenant-{user_id}", a_uuid)
        await reconcile_once(sm)
        await client.post(f"/api/v1/instances/{a_uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        assert (await get_instance(client, headers, a_uuid))["status"] == "stopped"
        resp = await client.delete(f"/api/v1/disks/{disk['uuid']}", headers=headers)
        assert resp.status_code == 200, resp.text
        async with sm() as session:
            from app.modules.orchestrator.models import Instance

            await session.execute(
                update(Instance).where(Instance.uuid == a_uuid).values(data_disk_id=disk["id"])
            )
            await session.commit()
        resp = await client.post(f"/api/v1/instances/{a_uuid}/start", headers=headers)
        assert resp.status_code == 400
        assert resp.json()["code"] == "VALIDATION_ERROR"

    async def test_cross_instance_mount(self, client, sm, fake):
        """实例释放后数据盘保留,可挂载到另一实例。"""
        headers, user_id, key_id = await funded_user(client, sm, "13500000010", "500.00")
        sku_id = await create_test_sku(sm)
        disk = await create_disk(client, headers)
        await drain(sm)

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
        pod = fake.pods[(f"tenant-{user_id}", a_uuid)]
        assert pod.spec.data_disk_pvc == f"disk-{disk['uuid']}"

        d = (await client.get("/api/v1/disks", headers=headers)).json()[0]
        assert d["mounted_instance_id"] is not None

        resp = await client.delete(f"/api/v1/disks/{disk['uuid']}", headers=headers)
        assert resp.json()["code"] == "DISK_IN_USE"

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
        headers, _user_id, _key = await funded_user(client, sm, "13500000020")
        await create_disk(client, headers, size_gb=100)
        async with sm() as session:
            await session.execute(update(DataDisk).values(created_at=now_utc() - timedelta(days=2)))
            await session.commit()

        assert await settle_daily_disks(sm) == 1
        assert await settle_daily_disks(sm) == 0
        async with sm() as session:
            bill = (await session.execute(select(BillDailyDisk))).scalar_one()
            entries = (await session.execute(select(BalanceLedger))).scalars().all()
        yesterday = (billing_day_floor(now_utc()) - timedelta(days=1) + BILLING_DAY_OFFSET).date()
        expected = disk_daily_charge(Decimal("0.0350"), 100, yesterday)
        assert bill.amount == expected
        assert len([e for e in entries if e.type == "consume"]) == 1

        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == str(Decimal("100.00") - expected)

    async def test_new_disk_not_billed_for_yesterday(self, client, sm, fake):
        headers, _user_id, _key = await funded_user(client, sm, "13500000021")
        await create_disk(client, headers)
        assert await settle_daily_disks(sm) == 0

    async def test_delete_same_day_pays_final_day(self, client, sm, fake):
        """当日建、当日删:出末日账。"""
        headers, _user_id, _key = await funded_user(client, sm, "13500000022")
        disk = await create_disk(client, headers, size_gb=100)
        await client.delete(f"/api/v1/disks/{disk['uuid']}", headers=headers)
        async with sm() as session:
            bill = (await session.execute(select(BillDailyDisk))).scalar_one()
        today = (billing_day_floor(now_utc()) + BILLING_DAY_OFFSET).date()
        expected = disk_daily_charge(Decimal("0.0350"), 100, today)
        assert bill.amount == expected
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == str(Decimal("100.00") - expected)

    async def test_delete_without_watermark_backfills_from_creation(self, client, sm, fake):
        """水位线缺失:删盘以建盘日为下界补结欠账天数。"""
        headers, _user_id, _key = await funded_user(client, sm, "13500000025")
        disk = await create_disk(client, headers, size_gb=100)
        async with sm() as session:
            await session.execute(update(DataDisk).values(created_at=now_utc() - timedelta(days=3)))
            await session.commit()
        resp = await client.delete(f"/api/v1/disks/{disk['uuid']}", headers=headers)
        assert resp.status_code == 200, resp.text
        async with sm() as session:
            bills = (
                (await session.execute(select(BillDailyDisk).order_by(BillDailyDisk.day)))
                .scalars()
                .all()
            )
        assert len(bills) == 4
        expected = sum(
            disk_daily_charge(
                Decimal("0.0350"),
                100,
                (billing_day_floor(now_utc()) - timedelta(days=k) + BILLING_DAY_OFFSET).date(),
            )
            for k in range(3, -1, -1)
        )
        assert sum(b.amount for b in bills) == expected

    async def test_expand_settles_old_size_first(self, client, sm, fake):
        """扩容前按旧容量结清未出账日期。"""
        headers, _user_id, _key = await funded_user(client, sm, "13500000023")
        disk = await create_disk(client, headers, size_gb=100)
        resp = await client.patch(
            f"/api/v1/disks/{disk['uuid']}", json={"size_gb": 1000}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        async with sm() as session:
            bill = (await session.execute(select(BillDailyDisk))).scalar_one()
        assert bill.size_gb == 100
        today = (billing_day_floor(now_utc()) + BILLING_DAY_OFFSET).date()
        assert bill.amount == disk_daily_charge(Decimal("0.0350"), 100, today)

    async def test_frozen_disk_delete_not_billed(self, client, sm, fake):
        """冻结态不计费:欠费回收删盘不补账。"""
        headers, _user_id, _key = await funded_user(client, sm, "13500000024")
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
    async def test_grace_frozen_reclaim_and_recovery(self, client, sm, fake):
        headers, user_id, _key = await funded_user(client, sm, "13500000030")
        await create_disk(client, headers)
        async with sm() as session:
            balance = await wallet.get_balance(session, user_id)
            await wallet.debit(
                session, user_id, balance, type_="adjust", remark="drain", allow_negative=True
            )
            await session.commit()
        counts = await balance_patrol(sm)
        assert counts["disks"] == 1
        d = (await client.get("/api/v1/disks", headers=headers)).json()[0]
        assert d["status"] == "grace"
        assert d["grace_started_at"] is not None

        async with sm() as session:
            await session.execute(
                update(DataDisk).values(grace_started_at=now_utc() - timedelta(days=8))
            )
            await session.commit()
        await balance_patrol(sm)
        d = (await client.get("/api/v1/disks", headers=headers)).json()[0]
        assert d["status"] == "frozen"

        async with sm() as session:
            await session.execute(
                update(DataDisk).values(frozen_started_at=now_utc() - timedelta(days=31))
            )
            await session.commit()
        await balance_patrol(sm)
        await drain(sm)
        disks = (await client.get("/api/v1/disks", headers=headers)).json()
        assert disks == []

    async def test_recharge_restores_frozen_disk(self, client, sm, fake):
        """充值后巡检将 frozen 数据盘恢复为 active。"""
        headers, user_id, _key = await funded_user(client, sm, "13500000032")
        await create_disk(client, headers)
        async with sm() as session:
            balance = await wallet.get_balance(session, user_id)
            await wallet.debit(
                session, user_id, balance, type_="adjust", remark="drain", allow_negative=True
            )
            await session.commit()
        await balance_patrol(sm)
        async with sm() as session:
            await session.execute(
                update(DataDisk).values(grace_started_at=now_utc() - timedelta(days=8))
            )
            await session.commit()
        await balance_patrol(sm)
        assert (await client.get("/api/v1/disks", headers=headers)).json()[0]["status"] == "frozen"

        async with sm() as session:
            await wallet.credit(session, user_id, Decimal("10.00"), type_="recharge")
            await session.commit()
        await balance_patrol(sm)
        d = (await client.get("/api/v1/disks", headers=headers)).json()[0]
        assert d["status"] == "active"
        assert d["frozen_started_at"] is not None and d["grace_started_at"] is not None


class TestDiskQuota:
    async def test_count_quota_blocks_creation(self, client, sm, fake, monkeypatch):
        from app.core.config import get_settings

        headers, _user_id, _key = await funded_user(client, sm, "13500000040")
        monkeypatch.setattr(get_settings(), "max_disks_per_user", 2, raising=False)
        await create_disk(client, headers, name="d1")
        await create_disk(client, headers, name="d2")
        resp = await client.post(
            "/api/v1/disks", json={"name": "d3", "size_gb": 100}, headers=headers
        )
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "disks.countQuota"

    async def test_capacity_quota_caps_total_size_on_create_and_expand(self, client, sm, fake):
        """策略 max_disk_gb_per_user 限未删除盘 size_gb 之和:建盘与扩容都校验,删盘释放额度。"""
        headers, _user_id, _key = await funded_user(client, sm, "13500000041")
        await set_platform_setting(sm, "max_disk_gb_per_user", "250")
        d1 = await create_disk(client, headers, name="d1", size_gb=100)
        d2 = await create_disk(client, headers, name="d2", size_gb=100)
        resp = await client.post(
            "/api/v1/disks", json={"name": "d3", "size_gb": 100}, headers=headers
        )
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "disks.capacityQuota"
        assert resp.json()["params"] == {"max": 250}

        resp = await client.patch(
            f"/api/v1/disks/{d1['uuid']}", json={"size_gb": 151}, headers=headers
        )
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "disks.capacityQuota"
        resp = await client.patch(
            f"/api/v1/disks/{d1['uuid']}", json={"size_gb": 150}, headers=headers
        )
        assert resp.status_code == 200, resp.text

        await client.delete(f"/api/v1/disks/{d2['uuid']}", headers=headers)
        await drain(sm)
        resp = await client.post(
            "/api/v1/disks", json={"name": "d3", "size_gb": 100}, headers=headers
        )
        assert resp.status_code == 201, resp.text


class TestDiskIdempotency:
    async def test_repeated_create_with_same_key_returns_same_disk(self, client, sm, fake):
        """同幂等键重放不多出一块盘。"""
        headers, _user_id, _key = await funded_user(client, sm, "13500000050")
        h = {**headers, "Idempotency-Key": "disk-idem-1"}
        a = await client.post("/api/v1/disks", json={"name": "d", "size_gb": 100}, headers=h)
        b = await client.post("/api/v1/disks", json={"name": "d", "size_gb": 100}, headers=h)
        assert a.status_code == 201 and b.status_code == 200
        assert b.headers["x-idempotent-replay"] == "true"
        assert a.json()["uuid"] == b.json()["uuid"]
        assert len((await client.get("/api/v1/disks", headers=headers)).json()) == 1

    async def test_same_key_different_params_409(self, client, sm, fake):
        """同键异参(改了容量):409。"""
        headers, _user_id, _key = await funded_user(client, sm, "13500000051")
        h = {**headers, "Idempotency-Key": "disk-idem-mix"}
        a = await client.post("/api/v1/disks", json={"name": "d", "size_gb": 100}, headers=h)
        assert a.status_code == 201
        b = await client.post("/api/v1/disks", json={"name": "d", "size_gb": 200}, headers=h)
        assert b.status_code == 409
        assert b.json()["message_key"] == "common.idempotencyKeyMismatch"
        assert len((await client.get("/api/v1/disks", headers=headers)).json()) == 1
