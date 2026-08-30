"""数据盘:CRUD/挂载生命周期/日结幂等/欠费链路。验收:跨实例挂载,释放实例盘保留。"""

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
    get_instance,
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
        # 擦除必须打在盘记录登记的那条子路径上,否则 rm -rf 静默成功而真实数据一字节未动
        assert fake.wiped_disks[-1] == (f"tenant-{user_id}", f"disk-{disk['uuid']}")

    async def test_create_requires_balance(self, client, sm, fake):
        headers, _user_id, _key = await create_user_with_key(client, "13500000001")
        resp = await client.post(
            "/api/v1/disks", json={"name": "d", "size_gb": 100}, headers=headers
        )
        assert resp.json()["code"] == "INSUFFICIENT_BALANCE"

    async def test_expand_requires_balance(self, client, sm, fake):
        """扩容与创建同一条燃烧率护栏:余额不足时不得把日费敞口免费放大
        (否则欠费用户可把盘扩到 disk_max_gb,日结照扣,形成事实透支)。"""
        headers, user_id, _key = await create_user_with_key(client, "13500000003")
        await fund_wallet(sm, user_id)  # 100.00
        disk = await create_disk(client, headers, size_gb=100)
        await drain(sm)
        # 余额压到接近零(模拟欠费):4096GB 的增量日费必然过不了护栏
        async with sm() as session:
            await wallet.debit(session, user_id, Decimal("99.99"), allow_negative=False)
            await session.commit()
        resp = await client.patch(
            f"/api/v1/disks/{disk['uuid']}", json={"size_gb": 4096}, headers=headers
        )
        assert resp.json()["code"] == "INSUFFICIENT_BALANCE"
        # 容量未被更新(校验与容量更新同一事务)
        d = (await client.get("/api/v1/disks", headers=headers)).json()[0]
        assert d["size_gb"] == 100

    async def test_size_limits(self, client, sm, fake):
        headers, user_id, _key = await create_user_with_key(client, "13500000002")
        await fund_wallet(sm, user_id)
        resp = await client.post("/api/v1/disks", json={"name": "d", "size_gb": 5}, headers=headers)
        assert resp.json()["code"] == "VALIDATION_ERROR"


class TestMountLifecycle:
    async def test_attach_rejected_until_quota_synced(self, client, sm, fake):
        """配额未下发成功的盘不得挂载:JuiceFS 目录硬配额是唯一容量强制点,
        无配额挂载 = 可写穿声明容量挤爆共享文件系统;同步完成后即可挂。"""
        headers, user_id, key_id = await create_user_with_key(client, "13500000013")
        await fund_wallet(sm, user_id, "500.00")
        sku_id = await create_test_sku(sm)
        disk = await create_disk(client, headers)
        # 不 drain:disk.quota 任务仍在途,quota_synced=false → 409
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
        assert resp.json()["message_key"] == "disks.quotaNotSynced"
        # 配额下发完成后挂载放行
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
        """停机→删盘→开机:挂载引用随删盘同事务摘除,开机不挂到擦除中的旧 subPath。"""
        headers, user_id, key_id = await create_user_with_key(client, "13500000011")
        await fund_wallet(sm, user_id, "500.00")
        sku_id = await create_test_sku(sm)
        disk = await create_disk(client, headers)
        await drain(sm)  # 配额下发完成(quota_synced=true)后才可挂载
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
        # 停机(数据盘保持挂载标记,删盘时按 stopped 自动解挂)
        await client.post(f"/api/v1/instances/{a_uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        assert (await get_instance(client, headers, a_uuid))["status"] == "stopped"
        # 删盘 → 引用摘除 + 擦除完成
        resp = await client.delete(f"/api/v1/disks/{disk['uuid']}", headers=headers)
        assert resp.status_code == 200, resp.text
        await drain(sm)
        async with sm() as session:
            from app.modules.orchestrator.models import Instance

            inst = (
                await session.execute(select(Instance).where(Instance.uuid == a_uuid))
            ).scalar_one()
            assert inst.data_disk_id is None
        # 开机:不带数据盘,不报错;新 Pod 无 subPath
        resp = await client.post(f"/api/v1/instances/{a_uuid}/start", headers=headers)
        assert resp.status_code == 200, resp.text
        await drain(sm)
        pod = fake.pods[(f"tenant-{user_id}", a_uuid)]
        assert pod.spec.data_disk_subpath is None

    async def test_start_rejected_when_disk_deleting(self, client, sm, fake):
        """盘处于 deleting(擦除中)时开机被拒绝:不能挂到正在被擦除的目录。"""
        headers, user_id, key_id = await create_user_with_key(client, "13500000012")
        await fund_wallet(sm, user_id, "500.00")
        sku_id = await create_test_sku(sm)
        disk = await create_disk(client, headers)
        await drain(sm)  # 配额下发完成后才可挂载
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
        # 删盘但不 drain:盘停在 deleting,引用已摘除 —— 手工恢复引用模拟存量数据/竞态
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
        """验收:A 挂载 → A 释放(盘保留)→ B 挂载同一块盘。"""
        headers, user_id, key_id = await create_user_with_key(client, "13500000010")
        await fund_wallet(sm, user_id, "500.00")
        sku_id = await create_test_sku(sm)
        disk = await create_disk(client, headers)
        await drain(sm)  # 配额下发完成后才可挂载

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
        # Pod spec 带 JuiceFS 子路径(挂 /root/data)。子路径的唯一事实源是
        # data_disks.juicefs_subpath(= disk-<uuid>),不是自增主键
        pod = fake.pods[(f"tenant-{user_id}", a_uuid)]
        assert pod.spec.data_disk_subpath == f"disk-{disk['uuid']}"

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
        # 日费按「前 k 天累计 − 前 k−1 天累计」出账(整月累计才精确等于月单价 × 天数 / 30),
        # 所以单日金额随当月第几天在 0.11/0.12 之间摆动 —— 不能写死某一个值
        yesterday = (billing_day_floor(now_utc()) - timedelta(days=1) + BILLING_DAY_OFFSET).date()
        expected = disk_daily_charge(Decimal("0.0350"), 100, yesterday)
        assert bill.amount == expected
        assert len([e for e in entries if e.type == "consume"]) == 1

        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == str(Decimal("100.00") - expected)

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
        await client.delete(f"/api/v1/disks/{disk['uuid']}", headers=headers)
        async with sm() as session:
            bill = (await session.execute(select(BillDailyDisk))).scalar_one()
        today = (billing_day_floor(now_utc()) + BILLING_DAY_OFFSET).date()
        expected = disk_daily_charge(Decimal("0.0350"), 100, today)
        assert bill.amount == expected
        w = (await client.get("/api/v1/wallet", headers=headers)).json()
        assert w["balance"] == str(Decimal("100.00") - expected)

    async def test_delete_without_watermark_backfills_from_creation(self, client, sm, fake):
        """水位线缺失(全新部署引导窗口):删盘以建盘日为下界补结欠账天数,
        而非只结当日(那是少收方向的静默免单)。"""
        headers, user_id, _key = await create_user_with_key(client, "13500000025")
        await fund_wallet(sm, user_id)
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
        assert len(bills) == 4  # 建盘日..当日,逐日一张
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
        today = (billing_day_floor(now_utc()) + BILLING_DAY_OFFSET).date()
        assert bill.amount == disk_daily_charge(Decimal("0.0350"), 100, today)

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
            await wallet.debit(
                session, user_id, balance, type_="adjust", remark="drain", allow_negative=True
            )
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

    async def test_recharge_restores_frozen_disk(self, client, sm, fake):
        """盘已经熬到 frozen 之后再充值,必须能解冻。

        巡检集合若拼成「有 active/grace 盘的用户 ∪ 余额≤0 的用户」,这类用户两边都不在。
        """
        headers, user_id, _key = await create_user_with_key(client, "13500000032")
        await fund_wallet(sm, user_id)
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
        # grace_started_at sticky:宽限钟累计不随充值清零(防「欠费→充值→再欠费」无限循环);
        # frozen_started_at 是删除倒计时,出冻结态即清零
        assert d["frozen_started_at"] is None and d["grace_started_at"] is not None


class TestDiskQuota:
    async def test_count_quota_blocks_creation(self, client, sm, fake, monkeypatch):
        from app.core.config import get_settings

        headers, user_id, _key = await create_user_with_key(client, "13500000040")
        await fund_wallet(sm, user_id)
        monkeypatch.setattr(get_settings(), "max_disks_per_user", 2, raising=False)
        await create_disk(client, headers, name="d1")
        await create_disk(client, headers, name="d2")
        resp = await client.post(
            "/api/v1/disks", json={"name": "d3", "size_gb": 100}, headers=headers
        )
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "disks.countQuota"


class TestDiskIdempotency:
    async def test_repeated_create_with_same_key_returns_same_disk(self, client, sm, fake):
        """响应丢失时用户按第二下,不能多出一块按日计费的孤儿盘。"""
        headers, user_id, _key = await create_user_with_key(client, "13500000050")
        await fund_wallet(sm, user_id)
        h = {**headers, "Idempotency-Key": "disk-idem-1"}
        a = await client.post("/api/v1/disks", json={"name": "d", "size_gb": 100}, headers=h)
        b = await client.post("/api/v1/disks", json={"name": "d", "size_gb": 100}, headers=h)
        assert a.status_code == 201 and b.status_code == 200
        assert b.headers["x-idempotent-replay"] == "true"
        assert a.json()["uuid"] == b.json()["uuid"]
        assert len((await client.get("/api/v1/disks", headers=headers)).json()) == 1

    async def test_same_key_different_params_409(self, client, sm, fake):
        """同键异参(改了容量):显式 409,绝不静默返回上一块盘(弱键复用防线)。"""
        headers, user_id, _key = await create_user_with_key(client, "13500000051")
        await fund_wallet(sm, user_id)
        h = {**headers, "Idempotency-Key": "disk-idem-mix"}
        a = await client.post("/api/v1/disks", json={"name": "d", "size_gb": 100}, headers=h)
        assert a.status_code == 201
        b = await client.post("/api/v1/disks", json={"name": "d", "size_gb": 200}, headers=h)
        assert b.status_code == 409
        assert b.json()["message_key"] == "common.idempotencyKeyMismatch"
        assert len((await client.get("/api/v1/disks", headers=headers)).json()) == 1
