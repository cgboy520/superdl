"""编排器加固:恢复边与超时边、悬挂逃逸、泄漏回收熔断、并发开户、幂等键、软准入、
结算候选、保留期 GC、欠费盘 grace、重启撞端口。"""

import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app.core.k8s import NodePortTaken
from app.core.outbox import RUNNING_TIMEOUT, OutboxTask
from app.core.timeutil import BILLING_DAY_OFFSET, billing_day_floor, now_utc
from app.modules.billing import wallet
from app.modules.billing.models import BillDailyDisk, BillHourly
from app.modules.billing.patrol import balance_patrol
from app.modules.billing.settlement import settle_daily_disks
from app.modules.orchestrator.models import DataDisk, Instance, InstanceEvent, PortAllocation
from app.modules.orchestrator.reconciler import reconcile_once
from tests.helpers import (
    H_END,
    IMAGE_PYTORCH,
    H,
    backdate_running_event,
    create_disk,
    create_test_sku,
    create_user_with_key,
    drain,
    fund_wallet,
    get_instance,
    provision_running,
    seed_instance,
    seed_node_spec,
)

pytestmark = pytest.mark.usefixtures("fake")


async def _raw_create(client, headers, sku_id, key_id, *, idem=None):
    h = dict(headers)
    if idem:
        h["Idempotency-Key"] = idem
    return await client.post(
        "/api/v1/instances",
        json={
            "sku_id": sku_id,
            "image_ref": IMAGE_PYTORCH,
            "ssh_key_ids": [key_id],
        },
        headers=h,
    )


async def _backdate_status(sm, uuid: str, to_status: str, age: timedelta) -> None:
    """把「进入某状态」的事件时刻回拨 age。"""
    async with sm() as session:
        inst_id = (
            await session.execute(select(Instance.id).where(Instance.uuid == uuid))
        ).scalar_one()
        await session.execute(
            update(InstanceEvent)
            .where(InstanceEvent.instance_id == inst_id, InstanceEvent.to_status == to_status)
            .values(created_at=now_utc() - age)
        )
        await session.commit()


async def _backdate_created(sm, uuid: str, age: timedelta) -> None:
    """把实例 created_at 回拨 age。"""
    async with sm() as session:
        await session.execute(
            update(Instance).where(Instance.uuid == uuid).values(created_at=now_utc() - age)
        )
        await session.commit()


class TestEnsurePortRace:
    async def test_concurrent_segment_extension_self_heals(self, sm):
        """空池并发扩段:on_conflict_do_nothing + 函数内重试,不抛 IntegrityError。"""
        import asyncio

        from app.modules.orchestrator.ports import ensure_port

        async def alloc(owner: int) -> int:
            async with sm() as session:
                port = await ensure_port(session, Instance(id=owner))
                await session.commit()
                return port

        ports = await asyncio.gather(*(alloc(owner) for owner in range(1, 5)))
        assert len(set(ports)) == 4  # 各得一个端口,无人失败
        async with sm() as session:
            rows = (await session.execute(select(PortAllocation))).scalars().all()
        assert {r.port: r.instance_id for r in rows} == {p: o for o, p in enumerate(ports, start=1)}


class TestBlockedPortRecheck:
    async def test_external_occupant_stays_blocked(self, sm, fake):
        """外部对象(无平台标签的 Service)占用的端口:复检看得见占用者,不放回池。"""
        from app.modules.orchestrator.models import PortAllocation

        fake.inject_external_port(31234)
        async with sm() as session:
            session.add(PortAllocation(port=31234, instance_id=None, blocked=True))
            await session.commit()
        counts = await reconcile_once(sm)
        assert counts["ports_unblocked"] == 0
        async with sm() as session:
            row = (
                await session.execute(select(PortAllocation).where(PortAllocation.port == 31234))
            ).scalar_one()
            assert row.blocked is True

    async def test_vacated_port_is_recovered(self, sm, fake):
        """占用消失的 blocked 端口放回池。"""
        from app.modules.orchestrator.models import PortAllocation

        async with sm() as session:
            session.add(PortAllocation(port=31235, instance_id=None, blocked=True))
            await session.commit()
        counts = await reconcile_once(sm)
        assert counts["ports_unblocked"] == 1
        async with sm() as session:
            row = (
                await session.execute(select(PortAllocation).where(PortAllocation.port == 31235))
            ).scalar_one()
            assert row.blocked is False


class TestStartingTimeout:
    async def test_starting_timeout_fails_and_keeps_disk(self, client, sm, fake):
        """starting 超时走 starting→failed 边:清 Pod、回收端口,实例盘保留。"""
        headers, uuid, user_id = await provision_running(client, sm, fake, "13900000115")
        ns = f"tenant-{user_id}"
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "stopped"
        disk_marker = fake.instance_disks[(ns, uuid)]

        await client.post(f"/api/v1/instances/{uuid}/start", headers=headers)
        await drain(sm)  # Pod 已建但永不 Ready(auto_ready=False)
        await _backdate_status(sm, uuid, "starting", timedelta(minutes=6))
        counts = await reconcile_once(sm)
        assert counts["to_failed"] == 1
        assert (await get_instance(client, headers, uuid))["status"] == "failed"
        assert (ns, uuid) not in fake.pods
        await drain(sm)  # 不入队 disk_cleanup:盘仍是原来那块
        assert fake.instance_disks[(ns, uuid)] == disk_marker
        async with sm() as session:
            ports = (await session.execute(select(PortAllocation.instance_id))).scalars().all()
        assert all(p is None for p in ports)  # 端口已回收


class TestFailedRecovery:
    async def test_start_from_failed_reuses_instance_disk(self, client, sm, fake):
        """failed → start 恢复边:复用同一块实例盘重开机。"""
        headers, uuid, user_id = await provision_running(client, sm, fake, "13900000101")
        ns = f"tenant-{user_id}"
        disk_marker = fake.instance_disks[(ns, uuid)]
        fake.kill_pod(ns, uuid)
        await reconcile_once(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "failed"

        resp = await client.post(f"/api/v1/instances/{uuid}/start", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "starting"
        await drain(sm)
        fake.mark_ready(ns, uuid)
        await reconcile_once(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "running"
        # 同一块盘,事件链留下恢复轨迹
        assert fake.instance_disks[(ns, uuid)] == disk_marker
        events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=headers)).json()[
            "items"
        ]
        chain = [(e["from_status"], e["to_status"]) for e in events]
        assert ("failed", "stopped") in chain
        assert ("stopped", "starting") in chain

    async def test_start_from_failed_rejects_unsynced_disk(self, client, sm, fake):
        """failed 恢复开机走挂载门禁:数据盘 quota_synced=false → 409,实例留在 failed。"""
        headers, user_id, key_id = await create_user_with_key(client, "13900000109")
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
        uuid = resp.json()["uuid"]
        ns = f"tenant-{user_id}"
        await drain(sm)
        fake.mark_ready(ns, uuid)
        await reconcile_once(sm)
        fake.kill_pod(ns, uuid)
        await reconcile_once(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "failed"
        # 扩容:quota_synced 回落 false(不 drain)
        resp = await client.patch(
            f"/api/v1/disks/{disk['uuid']}", json={"size_gb": 200}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["quota_synced"] is False

        resp = await client.post(f"/api/v1/instances/{uuid}/start", headers=headers)
        assert resp.status_code == 409, resp.text
        assert resp.json()["message_key"] == "disks.quotaNotSynced"
        assert (await get_instance(client, headers, uuid))["status"] == "failed"

    async def test_release_from_stuck_stopping(self, client, sm, fake):
        """关机悬挂时用户可直接释放(stopping → releasing 边)。"""
        headers, uuid, _user_id = await provision_running(client, sm, fake, "13900000102")
        resp = await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        assert resp.json()["status"] == "stopping"
        # 不 drain:删除任务在途,Pod 仍在
        resp = await client.delete(f"/api/v1/instances/{uuid}", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "releasing"
        await drain(sm)
        counts = await reconcile_once(sm)
        assert counts["to_released"] == 1


class TestReadyWithoutPort:
    async def test_ready_pod_without_port_not_promoted(self, client, sm, fake):
        """Pod 已 Ready 但 ssh_port 未落库:不推进 running,留在 creating 等超时转 failed。"""
        headers, user_id, key_id = await create_user_with_key(client, "13900000110")
        await fund_wallet(sm, user_id)
        sku_id = await create_test_sku(sm)
        resp = await _raw_create(client, headers, sku_id, key_id)
        uuid = resp.json()["uuid"]
        await drain(sm)  # 建 Pod + 端口落库
        async with sm() as session:
            await session.execute(
                update(Instance).where(Instance.uuid == uuid).values(ssh_port=None)
            )
            await session.commit()
        fake.mark_ready(f"tenant-{user_id}", uuid)
        counts = await reconcile_once(sm)
        assert counts["to_running"] == 0
        assert (await get_instance(client, headers, uuid))["status"] == "creating"
        # 超时兜底:转 failed 并清理 Pod
        await _backdate_status(sm, uuid, "creating", timedelta(minutes=6))
        counts = await reconcile_once(sm)
        assert counts["to_failed"] == 1
        assert (f"tenant-{user_id}", uuid) not in fake.pods


class TestStuckEscape:
    async def test_stopping_two_tier_escape(self, client, sm, fake):
        """stopping 悬挂:一档超时重发删除任务,二档超时 force 强删后收敛 stopped。"""
        headers, uuid, user_id = await provision_running(client, sm, fake, "13900000103")
        ns = f"tenant-{user_id}"
        fake.graceful_delete = True  # 优雅期内对象仍在 etcd
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        assert (ns, uuid) in fake.pods  # 优雅期,Pod 还在

        # 一档(> stopping_timeout_seconds):经 outbox 重发删除
        await _backdate_status(sm, uuid, "stopping", timedelta(minutes=11))
        counts = await reconcile_once(sm)
        assert counts["delete_requeued"] == 1
        assert counts["force_deleted"] == 0
        assert (await get_instance(client, headers, uuid))["status"] == "stopping"
        async with sm() as session:
            pending = (
                (
                    await session.execute(
                        select(OutboxTask).where(
                            OutboxTask.type == "instance.stop", OutboxTask.status == "pending"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(pending) == 1  # 重发一次,不堆重复任务

        # 二档(> 2×):force 强删
        await _backdate_status(sm, uuid, "stopping", timedelta(minutes=21))
        counts = await reconcile_once(sm)
        assert counts["force_deleted"] == 1
        assert (ns, uuid) not in fake.pods
        counts = await reconcile_once(sm)
        assert counts["to_stopped"] == 1
        assert (await get_instance(client, headers, uuid))["status"] == "stopped"
        # 停机不回收 SSH 端口
        assert (await get_instance(client, headers, uuid))["ssh_port"] is not None

    async def test_releasing_two_tier_escape(self, client, sm, fake):
        """releasing 悬挂:二档 force 强删后收敛 released(端口回池、实例盘销毁)。"""
        headers, uuid, user_id = await provision_running(client, sm, fake, "13900000104")
        ns = f"tenant-{user_id}"
        fake.graceful_delete = True
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        # 直接在 stopping 释放
        await client.delete(f"/api/v1/instances/{uuid}", headers=headers)
        await drain(sm)
        assert (ns, uuid) in fake.pods

        await _backdate_status(sm, uuid, "releasing", timedelta(minutes=21))
        counts = await reconcile_once(sm)
        assert counts["force_deleted"] == 1
        counts = await reconcile_once(sm)
        assert counts["to_released"] == 1
        assert (await get_instance(client, headers, uuid))["status"] == "released"
        await drain(sm)  # disk_cleanup outbox:实例盘销毁(两阶段)
        assert (ns, uuid) not in fake.instance_disks  # 实例盘已销毁

    async def test_stopping_reenqueue_ignores_expired_lease(self, client, sm, fake):
        """running 删除任务 locked_at 租约过期不算在途,悬挂判定照常重发。"""
        headers, uuid, _user_id = await provision_running(client, sm, fake, "13900000107")
        fake.graceful_delete = True
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)

        # running 行,locked_at 已超 RUNNING_TIMEOUT
        async with sm() as session:
            inst_id = (
                await session.execute(select(Instance.id).where(Instance.uuid == uuid))
            ).scalar_one()
            session.add(
                OutboxTask(
                    type="instance.stop",
                    payload={"instance_id": inst_id},
                    status="running",
                    locked_by="dead-worker",
                    locked_at=now_utc() - RUNNING_TIMEOUT - timedelta(seconds=1),
                )
            )
            await session.commit()
        await _backdate_status(sm, uuid, "stopping", timedelta(minutes=11))
        counts = await reconcile_once(sm)
        assert counts["delete_requeued"] == 1

    async def test_stopping_reenqueue_skips_fresh_lease(self, client, sm, fake):
        """running 行 locked_at 在租约内 = 在途,不堆重复任务。"""
        headers, uuid, _user_id = await provision_running(client, sm, fake, "13900000108")
        fake.graceful_delete = True
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)

        async with sm() as session:
            inst_id = (
                await session.execute(select(Instance.id).where(Instance.uuid == uuid))
            ).scalar_one()
            session.add(
                OutboxTask(
                    type="instance.stop",
                    payload={"instance_id": inst_id},
                    status="running",
                    locked_by="live-worker",
                    locked_at=now_utc(),
                )
            )
            await session.commit()
        await _backdate_status(sm, uuid, "stopping", timedelta(minutes=11))
        counts = await reconcile_once(sm)
        assert counts["delete_requeued"] == 0


class TestLeakReclaim:
    async def test_unknown_pod_ratio_trips_breaker(self, client, sm, fake):
        """未知 Pod 占比超阈 → 本轮回收熔断。"""
        _headers, uuid, user_id = await provision_running(client, sm, fake, "13900000105")
        ns = f"tenant-{user_id}"
        spec = fake.pods[(ns, uuid)].spec
        for i in range(4):  # 4 个 DB 无记录的 Pod,占比 4/5 > 50%
            fake.inject_leaked_pod(ns, f"unknown{i:024x}", spec)
        counts = await reconcile_once(sm)
        assert counts["leaked"] == 0
        assert len(fake.pods) == 5  # 一个没动

    async def test_stopped_instance_leftover_pod_force_reclaimed(self, client, sm, fake):
        """已 stopped 实例的残留 Pod 过宽限期被强删;宽限内不动。"""
        headers, uuid, user_id = await provision_running(client, sm, fake, "13900000106")
        ns = f"tenant-{user_id}"
        spec = fake.pods[(ns, uuid)].spec
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "stopped"
        fake.inject_leaked_pod(ns, uuid, spec)  # 停机的 Pod 又冒出来了
        counts = await reconcile_once(sm)
        assert counts["leaked"] == 0  # 宽限内(restart 窗口保护)
        await _backdate_status(sm, uuid, "stopped", timedelta(minutes=21))
        counts = await reconcile_once(sm)
        assert counts["leaked"] == 1
        assert (ns, uuid) not in fake.pods
        assert (await get_instance(client, headers, uuid))["status"] == "stopped"

    async def test_wipe_job_pod_not_counted_in_breaker_ratio(self, client, sm, fake):
        """wipe Job Pod 不进 unknown 占比(靠 job-name 豁免),真泄漏照删。"""
        _headers, uuid, user_id = await provision_running(client, sm, fake, "13900000114")
        ns = f"tenant-{user_id}"
        spec = fake.pods[(ns, uuid)].spec
        fake.auto_wipe = False
        with pytest.raises(RuntimeError, match="wipe in progress"):
            await fake.wipe_disk(ns, f"disk-{uuid}")  # 登记 wipe Job Pod(对齐 real 创建后抛错)
        fake.inject_leaked_pod(ns, "leaked000000000000000000", spec)  # DB 无记录的真泄漏
        counts = await reconcile_once(sm)
        assert counts["leaked"] == 1  # 熔断未触发(若把 wipe Pod 计入,2/3 > 50% 会熔断放行)
        assert counts["job_pod_skipped"] == 1
        assert len(fake.job_pods) == 1  # wipe Pod 完好
        counts = await reconcile_once(sm)  # 再来一轮:wipe Pod 仍不被当泄漏强删
        assert counts["leaked"] == 0 and len(fake.job_pods) == 1


class TestCreateCriticalSection:
    async def test_concurrent_create_second_rejected(self, client, sm, fake):
        """余额只够一台时,两台并发创建一台成功一台余额不足。"""
        headers, user_id, key_id = await create_user_with_key(client, "13900000111")
        await fund_wallet(sm, user_id, "1.68")  # 恰好一台一小时
        sku_id = await create_test_sku(sm)  # 1.68/时
        r1, r2 = await asyncio.gather(
            _raw_create(client, headers, sku_id, key_id),
            _raw_create(client, headers, sku_id, key_id),
        )
        outcomes = sorted([r1.status_code, r2.status_code])
        assert outcomes == [202, 400]
        rejected = r1 if r1.status_code != 202 else r2
        assert rejected.json()["code"] == "INSUFFICIENT_BALANCE"
        instances = (await client.get("/api/v1/instances", headers=headers)).json()["items"]
        assert len(instances) == 1

    async def test_concurrent_same_idempotency_key_single_instance(self, client, sm, fake):
        """同幂等键并发重放:只开一台;新建方 202,重放方 200 + X-Idempotent-Replay。"""
        headers, user_id, key_id = await create_user_with_key(client, "13900000112")
        await fund_wallet(sm, user_id, "500.00")
        sku_id = await create_test_sku(sm)
        r1, r2 = await asyncio.gather(
            _raw_create(client, headers, sku_id, key_id, idem="race-1"),
            _raw_create(client, headers, sku_id, key_id, idem="race-1"),
        )
        assert {r1.status_code, r2.status_code} == {200, 202}, (r1.text, r2.text)
        replayed = r1 if r1.status_code == 200 else r2
        assert replayed.headers["x-idempotent-replay"] == "true"
        assert r1.json()["uuid"] == r2.json()["uuid"]
        instances = (await client.get("/api/v1/instances", headers=headers)).json()["items"]
        assert len(instances) == 1

    async def test_idempotency_key_expires_after_24h(self, client, sm, fake):
        """幂等键 24h 窗口:窗外同一键按新单处理。"""
        headers, user_id, key_id = await create_user_with_key(client, "13900000113")
        await fund_wallet(sm, user_id, "500.00")
        sku_id = await create_test_sku(sm)
        r1 = await _raw_create(client, headers, sku_id, key_id, idem="day-key")
        assert r1.status_code == 202
        async with sm() as session:
            await session.execute(
                update(Instance).values(created_at=now_utc() - timedelta(hours=25))
            )
            await session.commit()
        r2 = await _raw_create(client, headers, sku_id, key_id, idem="day-key")
        assert r2.status_code == 202, r2.text
        assert r2.json()["uuid"] != r1.json()["uuid"]
        async with sm() as session:
            old = (
                await session.execute(select(Instance).where(Instance.uuid == r1.json()["uuid"]))
            ).scalar_one()
        assert old.idempotency_key is None  # 旧记录已让出键位

    async def test_soft_admission_no_capacity(self, client, sm, fake):
        """软准入:(池,型号) 可分配量为 0 → 409 NO_CAPACITY。"""
        headers, user_id, key_id = await create_user_with_key(client, "13900000114")
        await fund_wallet(sm, user_id)
        sku_id = await create_test_sku(sm)
        await seed_node_spec(sm, gpu_count=1, gpu_used=1)  # hami 池唯一一张卡已占满
        resp = await _raw_create(client, headers, sku_id, key_id)
        assert resp.status_code == 409
        assert resp.json()["code"] == "NO_CAPACITY"


class TestBillingCandidatesCompleteness:
    async def test_candidates_cover_all_running_segments(self, sm):
        """结算候选 = 当前 running ∪ 窗口内/后离开 running 的实例。"""
        a, _ = await seed_instance(  # 窗口前进入,至今仍 running
            sm,
            user_id=1,
            events=[(H - timedelta(hours=1), "creating", "running")],
            status="running",
        )
        b, _ = await seed_instance(  # 窗口内离开 running
            sm,
            user_id=2,
            events=[
                (H - timedelta(hours=1), "creating", "running"),
                (H + timedelta(minutes=30), "running", "stopping"),
            ],
        )
        c, _ = await seed_instance(  # 窗口末仍 running,之后才停机(核心回归)
            sm,
            user_id=3,
            events=[
                (H - timedelta(hours=1), "creating", "running"),
                (H_END + timedelta(minutes=30), "running", "stopping"),
            ],
        )
        d, _ = await seed_instance(  # 从未 running(窗口内 creating 即失败)
            sm, user_id=4, events=[(H + timedelta(minutes=5), None, "creating")]
        )
        e, _ = await seed_instance(  # 窗口前已完整跑完离开
            sm,
            user_id=5,
            events=[
                (H - timedelta(hours=3), "creating", "running"),
                (H - timedelta(hours=2), "running", "stopping"),
            ],
        )
        from app.modules.orchestrator import service as orchestrator_service

        async with sm() as session:
            candidates = await orchestrator_service.billing_candidates(session, H, H_END)
        ids = {row[0] for row in candidates}
        assert ids == {a, b, c}
        assert d not in ids and e not in ids


class TestRetentionGC:
    async def test_failed_instance_gc_after_retention(self, client, sm, fake):
        """failed 超保留期(默认 7 天)自动释放并通知。"""
        headers, uuid, user_id = await provision_running(client, sm, fake, "13900000121")
        ns = f"tenant-{user_id}"
        fake.kill_pod(ns, uuid)
        await reconcile_once(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "failed"
        assert (ns, uuid) in fake.instance_disks

        await _backdate_status(sm, uuid, "failed", timedelta(days=8))
        await _backdate_created(sm, uuid, timedelta(days=8))
        counts = await reconcile_once(sm)
        assert counts["gc_released"] == 1
        assert (await get_instance(client, headers, uuid))["status"] == "releasing"
        notes = (await client.get("/api/v1/notifications", headers=headers)).json()["items"]
        assert any("失败实例已自动释放" in n["title"] for n in notes)
        await drain(sm)
        await reconcile_once(sm)
        await drain(sm)  # disk_cleanup outbox:实例盘销毁(两阶段)
        assert (ns, uuid) not in fake.instance_disks

    async def test_stopped_instance_gc_warn_then_reclaim(self, client, sm, fake):
        """stopped 保留期(默认 30 天):先预警(默认提前 7 天)再自动释放。"""
        headers, uuid, _user_id = await provision_running(client, sm, fake, "13900000122")
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "stopped"

        await _backdate_status(sm, uuid, "stopped", timedelta(days=24))
        await _backdate_created(sm, uuid, timedelta(days=24))
        counts = await reconcile_once(sm)
        assert counts["gc_warned"] == 1 and counts["gc_released"] == 0
        assert (await get_instance(client, headers, uuid))["status"] == "stopped"
        notes = (await client.get("/api/v1/notifications", headers=headers)).json()["items"]
        assert any("即将到期释放" in n["title"] for n in notes)

        await _backdate_status(sm, uuid, "stopped", timedelta(days=31))
        counts = await reconcile_once(sm)
        assert counts["gc_released"] == 1
        assert (await get_instance(client, headers, uuid))["status"] == "releasing"


class TestDiskArrearsHardening:
    async def _drain_wallet(self, sm, user_id: int) -> None:
        async with sm() as session:
            balance = await wallet.get_balance(session, user_id)
            await wallet.debit(
                session, user_id, balance, type_="adjust", remark="drain", allow_negative=True
            )
            await session.commit()

    async def test_grace_days_not_billed(self, client, sm, fake):
        """grace 停计费:宽限日不出账,回款恢复后也不补回。"""
        headers, user_id, _key = await create_user_with_key(client, "13900000131")
        await fund_wallet(sm, user_id)
        disk = await create_disk(client, headers)
        t0 = now_utc()
        await self._drain_wallet(sm, user_id)
        await balance_patrol(sm)  # active → grace(进 grace 结清当日)
        async with sm() as session:
            d = (
                await session.execute(select(DataDisk).where(DataDisk.uuid == disk["uuid"]))
            ).scalar_one()
            assert d.status == "grace"
            billed_days = {
                (b.day + BILLING_DAY_OFFSET).date()
                for b in (await session.execute(select(BillDailyDisk))).scalars().all()
            }
        billing_t0 = (billing_day_floor(t0) + BILLING_DAY_OFFSET).date()
        assert billing_t0 in billed_days  # 进 grace 当日已结清

        # 宽限中推进两天日结:不出账
        await settle_daily_disks(sm, at=t0 + timedelta(days=3))
        # 回款恢复 active,再推进两天:只结恢复后的日子
        async with sm() as session:
            await wallet.credit(session, user_id, Decimal("10.00"), type_="recharge")
            await session.commit()
        await balance_patrol(sm)
        await settle_daily_disks(sm, at=t0 + timedelta(days=5))
        async with sm() as session:
            days = {
                (b.day + BILLING_DAY_OFFSET).date()
                for b in (await session.execute(select(BillDailyDisk))).scalars().all()
            }

        def _billing_date(dt) -> object:
            return (billing_day_floor(dt) + BILLING_DAY_OFFSET).date()

        assert _billing_date(t0 + timedelta(days=1)) not in days  # grace 日
        assert _billing_date(t0 + timedelta(days=2)) not in days  # grace 日
        assert _billing_date(t0 + timedelta(days=3)) in days  # 恢复后正常出账

    async def test_catchup_across_grace_records_gaps(self, client, sm, fake):
        """日结停摆跨越 grace 转换:宽限区间内部日不出账且登记 grace_overlap 缺口;
        边界日(进入/恢复当日)照常出账。"""
        from app.modules.billing.models import SettlementGap
        from app.modules.billing.settlement import _advance_watermark

        headers, user_id, _key = await create_user_with_key(client, "13900000132")
        await fund_wallet(sm, user_id)
        await create_disk(client, headers)
        t0 = now_utc()
        t_day = billing_day_floor(t0)
        # 盘创建于 4 个计费日前;grace 区间 [T-3, T-1];水位线停在 T-4;盘已恢复 active
        async with sm() as session:
            await session.execute(
                update(DataDisk).values(
                    created_at=t0 - timedelta(days=4),
                    grace_started_at=t_day - timedelta(days=3),
                    grace_ended_at=t_day - timedelta(days=1),
                )
            )
            await session.commit()
        await _advance_watermark(sm, "daily_disk", t_day - timedelta(days=4))
        await settle_daily_disks(sm)  # 追平窗口:T-3、T-2、T-1

        async with sm() as session:
            billed = {
                (b.day + BILLING_DAY_OFFSET).date()
                for b in (await session.execute(select(BillDailyDisk))).scalars().all()
            }
            gaps = list(
                (
                    await session.execute(
                        select(SettlementGap).where(
                            SettlementGap.kind == "daily_disk",
                            SettlementGap.reason == "grace_overlap",
                        )
                    )
                ).scalars()
            )
        day = lambda back: (t_day - timedelta(days=back) + BILLING_DAY_OFFSET).date()  # noqa: E731
        assert billed == {day(3), day(1)}  # 边界日照常出账
        assert {(g.window_start + BILLING_DAY_OFFSET).date() for g in gaps} == {day(2)}
        # 幂等:再跑一轮不重复扣款、不重复登记
        await settle_daily_disks(sm)
        async with sm() as session:
            billed2 = (await session.execute(select(BillDailyDisk))).scalars().all()
            gaps2 = (
                (
                    await session.execute(
                        select(SettlementGap).where(SettlementGap.reason == "grace_overlap")
                    )
                )
                .scalars()
                .all()
            )
        assert len(billed2) == 2
        assert len(gaps2) == 1

    async def test_grace_clock_not_reset_by_recharge(self, client, sm, fake):
        """充值恢复不清零 grace_started_at。"""
        headers, user_id, _key = await create_user_with_key(client, "13900000133")
        await fund_wallet(sm, user_id)
        disk = await create_disk(client, headers)
        await self._drain_wallet(sm, user_id)
        await balance_patrol(sm)
        async with sm() as session:
            d1 = (
                await session.execute(select(DataDisk).where(DataDisk.uuid == disk["uuid"]))
            ).scalar_one()
            assert d1.status == "grace"
            first_grace_at = d1.grace_started_at
            assert first_grace_at is not None
        # 充值 → active → 再欠费 → 再 grace:起点仍是第一次
        async with sm() as session:
            await wallet.credit(session, user_id, Decimal("10.00"), type_="recharge")
            await session.commit()
        await balance_patrol(sm)
        await self._drain_wallet(sm, user_id)
        await balance_patrol(sm)
        async with sm() as session:
            d2 = (
                await session.execute(select(DataDisk).where(DataDisk.uuid == disk["uuid"]))
            ).scalar_one()
            assert d2.status == "grace"
            assert d2.grace_started_at == first_grace_at

    async def test_wipe_namespace_missing_is_done(self, client, sm, fake, monkeypatch):
        """租户 ns 不存在时擦盘视为完成。"""

        class _Api404(Exception):
            status = 404

        async def raise404(namespace, subpath):
            raise _Api404("namespace not found")

        monkeypatch.setattr(fake, "wipe_disk", raise404)
        headers, user_id, _key = await create_user_with_key(client, "13900000134")
        await fund_wallet(sm, user_id)
        disk = await create_disk(client, headers)
        await client.delete(f"/api/v1/disks/{disk['uuid']}", headers=headers)
        await drain(sm)
        assert (await client.get("/api/v1/disks", headers=headers)).json() == []


class TestRestartPortConflict:
    async def test_port_conflict_keeps_tail_bill(self, client, sm, fake, monkeypatch):
        """重启撞 NodePortTaken:stopping→stopped 与尾账已独立提交,不被回滚吞掉。"""
        headers, uuid, user_id = await provision_running(client, sm, fake, "13900000141")
        await backdate_running_event(sm, uuid, 30)  # 已跑约 30 分钟,尾账非零
        original = fake.create_instance
        fired = {"hit": False}

        async def guarded(spec):
            if spec.name == uuid and not fired["hit"]:
                fired["hit"] = True
                raise NodePortTaken(spec.ssh_node_port)
            await original(spec)

        monkeypatch.setattr(fake, "create_instance", guarded)
        await client.post(f"/api/v1/instances/{uuid}/restart", headers=headers)
        await drain(sm)  # 第一次:STOPPED 落库 → STARTING 落库 → 撞端口回滚

        data = await get_instance(client, headers, uuid)
        # 撞端口回滚后停在 starting(不回退到 stopping),重试续建
        assert data["status"] == "starting"
        events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=headers)).json()[
            "items"
        ]
        chain = [(e["from_status"], e["to_status"]) for e in events]
        assert ("stopping", "stopped") in chain
        assert ("stopped", "starting") in chain
        async with sm() as session:
            bill = (
                await session.execute(
                    select(BillHourly)
                    .join(Instance, Instance.id == BillHourly.instance_id)
                    .where(Instance.uuid == uuid)
                )
            ).scalar_one()
            assert bill.seconds_used > 0  # 尾账在案

        # 退避重试:换端口完成重启
        async with sm() as session:
            await session.execute(
                update(OutboxTask)
                .where(OutboxTask.type == "instance.restart")
                .values(next_retry_at=now_utc())
            )
            await session.commit()
        await drain(sm)
        fake.mark_ready(f"tenant-{user_id}", uuid)
        await reconcile_once(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "running"
