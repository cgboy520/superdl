"""编排器审计加固(P0 批次)的回归套件。

每条用例对应一处修复:它挂了,说明那处修复被改回去了。
覆盖:状态机全边表、failed 恢复边、stopping/releasing 悬挂两档超时逃逸、泄漏回收
熔断与 force、并发开户临界区、幂等键并发与 24h 窗、(池,型号) 软准入、结算候选
完备性、保留期 GC、欠费盘 grace 停计费与计时累计、重启撞端口不丢尾账。
"""

import asyncio
from datetime import timedelta
from decimal import Decimal
from typing import ClassVar

import pytest
from sqlalchemy import select, update

from app.core.k8s import NodePortTaken, set_orchestrator
from app.core.k8s.fake import FakeOrchestrator
from app.core.outbox import OutboxTask, drain
from app.core.timeutil import now_utc
from app.modules.billing import wallet
from app.modules.billing.models import BillDailyDisk, BillHourly
from app.modules.billing.patrol import balance_patrol
from app.modules.billing.settlement import settle_daily_disks
from app.modules.orchestrator.models import DataDisk, Instance, InstanceEvent
from app.modules.orchestrator.reconciler import reconcile_once
from app.modules.orchestrator.statemachine import TRANSITIONS
from tests.helpers import create_test_sku, create_user_with_key, fund_wallet, seed_node_spec
from tests.test_orchestrator_lifecycle import _provision_running, get_instance

pytestmark = pytest.mark.usefixtures("fake")


@pytest.fixture
def fake():
    orch = FakeOrchestrator(auto_ready=False)
    set_orchestrator(orch)
    yield orch
    set_orchestrator(None)


async def _raw_create(client, headers, sku_id, key_id, *, idem=None):
    h = dict(headers)
    if idem:
        h["Idempotency-Key"] = idem
    return await client.post(
        "/api/v1/instances",
        json={
            "sku_id": sku_id,
            "image_ref": "registry.superdl.local/pytorch:2.9.0-cu128",
            "ssh_key_ids": [key_id],
        },
        headers=h,
    )


async def _backdate_status(sm, uuid: str, to_status: str, age: timedelta) -> None:
    """把「进入某状态」的事件时刻回拨 age(悬挂/超时用例的时间快进)。"""
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


class TestStateMachineTable:
    # 全量合法边(加边/减边都必须改这张表,测试才跟着红)
    EXPECTED: ClassVar[set[tuple[str, str]]] = {
        ("creating", "running"),
        ("creating", "failed"),
        ("creating", "releasing"),
        ("running", "stopping"),
        ("running", "failed"),
        ("stopping", "stopped"),
        ("stopping", "releasing"),
        ("stopped", "starting"),
        ("stopped", "frozen"),
        ("stopped", "releasing"),
        ("starting", "running"),
        ("starting", "failed"),
        ("frozen", "stopped"),
        ("frozen", "releasing"),
        ("failed", "stopped"),
        ("failed", "releasing"),
        ("releasing", "released"),
    }

    def test_transition_table_is_exact(self):
        """TRANSITIONS 与预期边集逐条一致(挂了 = 有人改了状态机,先确认计费边影响)。"""
        pairs = {(f, t) for f, tos in TRANSITIONS.items() for t in tos}
        assert pairs == self.EXPECTED

    def test_every_pair_enforced(self):
        """全 9×9 枚举:合法边放行,非法边一律 INSTANCE_INVALID_TRANSITION。"""
        from app.core.errors import AppError
        from app.modules.orchestrator.statemachine import validate_transition

        statuses = set(TRANSITIONS)
        for from_s in statuses:
            for to_s in statuses:
                if (from_s, to_s) in self.EXPECTED:
                    validate_transition(from_s, to_s)
                else:
                    with pytest.raises(AppError):
                        validate_transition(from_s, to_s)


class TestFailedRecovery:
    async def test_start_from_failed_reuses_instance_disk(self, client, sm, fake):
        """failed → start 恢复边:复用同一块实例盘重开机(挂了 = 用户只能销毁数据重开)。"""
        headers, uuid, user_id = await _provision_running(client, sm, fake, "13900000101")
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
        # 同一块盘(fake 的标记值不变),事件链留下恢复轨迹
        assert fake.instance_disks[(ns, uuid)] == disk_marker
        events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=headers)).json()[
            "items"
        ]
        chain = [(e["from_status"], e["to_status"]) for e in events]
        assert ("failed", "stopped") in chain
        assert ("stopped", "starting") in chain

    async def test_release_from_stuck_stopping(self, client, sm, fake):
        """关机悬挂时用户可直接释放(stopping → releasing 边;挂了 = 悬挂实例永远删不掉)。"""
        headers, uuid, _user_id = await _provision_running(client, sm, fake, "13900000102")
        resp = await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        assert resp.json()["status"] == "stopping"
        # 不 drain:删除任务在途,Pod 仍在 —— 此时用户选择直接释放
        resp = await client.delete(f"/api/v1/instances/{uuid}", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "releasing"
        await drain(sm)
        counts = await reconcile_once(sm)
        assert counts["to_released"] == 1


class TestStuckEscape:
    async def test_stopping_two_tier_escape(self, client, sm, fake):
        """stopping 悬挂:一档超时重发删除任务,二档超时 force 强删后正常收敛 stopped。

        挂了 = 删除任务死信/丢失时实例永远卡在 stopping,尾账永远合不上。
        """
        headers, uuid, user_id = await _provision_running(client, sm, fake, "13900000103")
        ns = f"tenant-{user_id}"
        fake.graceful_delete = True  # 优雅期内对象仍在 etcd
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        assert (ns, uuid) in fake.pods  # 优雅期,Pod 还在

        # 一档(> stopping_timeout_seconds,默认 10min):经 outbox 重发删除
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

        # 二档(> 2×):force 强删,失联节点上的优雅删除永远完不成
        await _backdate_status(sm, uuid, "stopping", timedelta(minutes=21))
        counts = await reconcile_once(sm)
        assert counts["force_deleted"] == 1
        assert (ns, uuid) not in fake.pods
        counts = await reconcile_once(sm)
        assert counts["to_stopped"] == 1
        assert (await get_instance(client, headers, uuid))["status"] == "stopped"
        # 端口保留:停机不回收 SSH 端口
        assert (await get_instance(client, headers, uuid))["ssh_port"] is not None

    async def test_releasing_two_tier_escape(self, client, sm, fake):
        """releasing 悬挂:二档 force 强删后收敛 released(端口回池、实例盘销毁)。"""
        headers, uuid, user_id = await _provision_running(client, sm, fake, "13900000104")
        ns = f"tenant-{user_id}"
        fake.graceful_delete = True
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        # 不经 stopped:直接在 stopping 释放(覆盖悬挂释放链路)
        await client.delete(f"/api/v1/instances/{uuid}", headers=headers)
        await drain(sm)
        assert (ns, uuid) in fake.pods

        await _backdate_status(sm, uuid, "releasing", timedelta(minutes=21))
        counts = await reconcile_once(sm)
        assert counts["force_deleted"] == 1
        counts = await reconcile_once(sm)
        assert counts["to_released"] == 1
        assert (await get_instance(client, headers, uuid))["status"] == "released"
        assert (ns, uuid) not in fake.instance_disks  # 实例盘已销毁


class TestLeakReclaim:
    async def test_unknown_pod_ratio_trips_breaker(self, client, sm, fake):
        """未知 Pod 占比超阈 → 本轮回收熔断(挂了 = 接错集群/标签漂移时按陌生清单批量强删)。"""
        _headers, uuid, user_id = await _provision_running(client, sm, fake, "13900000105")
        ns = f"tenant-{user_id}"
        spec = fake.pods[(ns, uuid)].spec
        for i in range(4):  # 4 个 DB 无记录的 Pod,占比 4/5 > 50%
            fake.inject_leaked_pod(ns, f"unknown{i:024x}", spec)
        counts = await reconcile_once(sm)
        assert counts["leaked"] == 0
        assert len(fake.pods) == 5  # 一个没动

    async def test_stopped_instance_leftover_pod_force_reclaimed(self, client, sm, fake):
        """已 stopped 实例的残留 Pod 被强删回收(挂了 = 停机后泄漏的 Pod 白送算力)。"""
        headers, uuid, user_id = await _provision_running(client, sm, fake, "13900000106")
        ns = f"tenant-{user_id}"
        spec = fake.pods[(ns, uuid)].spec
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "stopped"
        fake.inject_leaked_pod(ns, uuid, spec)  # 停机的 Pod 又冒出来了
        counts = await reconcile_once(sm)
        assert counts["leaked"] == 1
        assert (ns, uuid) not in fake.pods
        assert (await get_instance(client, headers, uuid))["status"] == "stopped"


class TestCreateCriticalSection:
    async def test_concurrent_create_second_rejected(self, client, sm, fake):
        """并发开户临界区:余额只够一台时,两台并发创建必须一台成功一台余额不足。

        挂了 = 钱包锁/在途统计任一失守,串行开户可以绕过余额护栏透支多台。
        """
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
        instances = (await client.get("/api/v1/instances", headers=headers)).json()
        assert len(instances) == 1

    async def test_concurrent_same_idempotency_key_single_instance(self, client, sm, fake):
        """同幂等键并发重放:只开一台,两个请求拿到同一台(挂了 = 唯一约束竞争变 500)。"""
        headers, user_id, key_id = await create_user_with_key(client, "13900000112")
        await fund_wallet(sm, user_id, "500.00")
        sku_id = await create_test_sku(sm)
        r1, r2 = await asyncio.gather(
            _raw_create(client, headers, sku_id, key_id, idem="race-1"),
            _raw_create(client, headers, sku_id, key_id, idem="race-1"),
        )
        assert r1.status_code == 202 and r2.status_code == 202, (r1.text, r2.text)
        assert r1.json()["uuid"] == r2.json()["uuid"]
        instances = (await client.get("/api/v1/instances", headers=headers)).json()
        assert len(instances) == 1

    async def test_idempotency_key_expires_after_24h(self, client, sm, fake):
        """幂等键 24h 窗口:窗外同一键按新单处理,旧记录让出键位。

        挂了 = 键位永久占用,隔天重试同键拿到的是昨天那台(或撞唯一约束 500)。
        """
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
        """软准入:台账明确 (池,型号) 可分配量为 0 → 409 NO_CAPACITY,不放进调度干等超时。

        挂了 = 售罄池的创建请求全部 creating 5 分钟后转 failed,且占着配额。
        """
        headers, user_id, key_id = await create_user_with_key(client, "13900000114")
        await fund_wallet(sm, user_id)
        sku_id = await create_test_sku(sm)
        await seed_node_spec(sm, gpu_count=1, gpu_used=1)  # hami 池唯一一张卡已占满
        resp = await _raw_create(client, headers, sku_id, key_id)
        assert resp.status_code == 409
        assert resp.json()["code"] == "NO_CAPACITY"


class TestBillingCandidatesCompleteness:
    async def test_candidates_cover_all_running_segments(self, sm):
        """结算候选 = 当前 running ∪ 窗口内/后离开 running 的实例。

        覆盖「窗口末仍 running、之后才停机」:这类实例靠 from_status='running' 事件命中,
        漏了就是少结账(平台亏钱)。
        """
        from tests.test_billing_settlement import H_END, H, seed_instance

        a = await seed_instance(  # 窗口前进入,至今仍 running
            sm,
            user_id=1,
            events=[(H - timedelta(hours=1), "creating", "running")],
            status="running",
        )
        b = await seed_instance(  # 窗口内离开 running
            sm,
            user_id=2,
            events=[
                (H - timedelta(hours=1), "creating", "running"),
                (H + timedelta(minutes=30), "running", "stopping"),
            ],
        )
        c = await seed_instance(  # 窗口末仍 running,之后才停机(核心回归)
            sm,
            user_id=3,
            events=[
                (H - timedelta(hours=1), "creating", "running"),
                (H_END + timedelta(minutes=30), "running", "stopping"),
            ],
        )
        d = await seed_instance(  # 从未 running(窗口内 creating 即失败)
            sm, user_id=4, events=[(H + timedelta(minutes=5), None, "creating")]
        )
        e = await seed_instance(  # 窗口前已完整跑完离开
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
        """failed 超保留期(默认 7 天)自动释放并通知(挂了 = 失败实例盘 PVC 永久泄漏)。"""
        headers, uuid, user_id = await _provision_running(client, sm, fake, "13900000121")
        ns = f"tenant-{user_id}"
        fake.kill_pod(ns, uuid)
        await reconcile_once(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "failed"
        assert (ns, uuid) in fake.instance_disks

        await _backdate_status(sm, uuid, "failed", timedelta(days=8))
        counts = await reconcile_once(sm)
        assert counts["gc_released"] == 1
        assert (await get_instance(client, headers, uuid))["status"] == "releasing"
        notes = (await client.get("/api/v1/notifications", headers=headers)).json()["items"]
        assert any("失败实例已自动释放" in n["title"] for n in notes)
        await drain(sm)
        await reconcile_once(sm)
        assert (ns, uuid) not in fake.instance_disks

    async def test_stopped_instance_gc_warn_then_reclaim(self, client, sm, fake):
        """stopped 保留期(默认 30 天):先预警(默认提前 7 天)再自动释放。

        挂了 = 有偿用户停机盘无限免费占用,或未预警直接删数据。
        """
        headers, uuid, _user_id = await _provision_running(client, sm, fake, "13900000122")
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "stopped"

        await _backdate_status(sm, uuid, "stopped", timedelta(days=24))
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
        """grace(欠费宽限)停计费:宽限日子不出账,回款恢复后也不补回。

        挂了 = 欠费用户的盘继续累计欠费(grace 计费),或水位线越过宽限日后被追回补回。
        """
        from tests.test_disks import create_disk

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
                b.day.date() for b in (await session.execute(select(BillDailyDisk))).scalars().all()
            }
        assert t0.date() in billed_days  # 进 grace 当日已结清

        # 宽限中推进两天日结:grace 盘不在计费集合,不出账
        await settle_daily_disks(sm, at=t0 + timedelta(days=3))
        # 回款恢复 active,再推进两天:只结恢复后的日子,宽限日不补回
        async with sm() as session:
            await wallet.credit(session, user_id, Decimal("10.00"), type_="recharge")
            await session.commit()
        await balance_patrol(sm)
        await settle_daily_disks(sm, at=t0 + timedelta(days=5))
        async with sm() as session:
            days = {
                b.day.date() for b in (await session.execute(select(BillDailyDisk))).scalars().all()
            }
        assert (t0 + timedelta(days=1)).date() not in days  # grace 日
        assert (t0 + timedelta(days=2)).date() not in days  # grace 日
        assert (t0 + timedelta(days=3)).date() in days  # 恢复后正常出账

    async def test_grace_clock_not_reset_by_recharge(self, client, sm, fake):
        """宽限钟累计:充值恢复不清零 grace_started_at(挂了 = 欠费-充值循环永远不到 frozen)。"""
        from tests.test_disks import create_disk

        headers, user_id, _key = await create_user_with_key(client, "13900000132")
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

    async def test_delete_disk_of_stopped_instance(self, client, sm, fake):
        """挂载实例已 stopped/failed 时允许删盘并自动解挂(挂了 = 实例卡着盘就删不掉还按日计费)。"""
        from tests.test_disks import create_disk

        headers, user_id, key_id = await create_user_with_key(client, "13900000133")
        await fund_wallet(sm, user_id, "500.00")
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
        assert resp.status_code == 202, resp.text
        uuid = resp.json()["uuid"]
        await drain(sm)
        fake.mark_ready(f"tenant-{user_id}", uuid)
        await reconcile_once(sm)
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)

        resp = await client.delete(f"/api/v1/disks/{disk['uuid']}", headers=headers)
        assert resp.status_code == 200, resp.text  # stopped 实例的盘可删
        assert resp.json()["mounted_instance_id"] is None
        await drain(sm)
        assert (await client.get("/api/v1/disks", headers=headers)).json() == []

    async def test_wipe_namespace_missing_is_done(self, client, sm, fake, monkeypatch):
        """租户 ns 不存在(从未建过实例)时擦盘视为完成(挂了 = 这类删盘任务全进死信)。"""
        from tests.test_disks import create_disk

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
        """重启撞 NodePortTaken:stopping→stopped 与尾账已独立提交,不被端口补偿回滚吞掉。

        挂了 = 尾账丢失(少计停机前费用)且实例卡在 stopping 无法自愈。
        """
        from tests.test_billing_flow import backdate_running_event

        headers, uuid, user_id = await _provision_running(client, sm, fake, "13900000141")
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
        await drain(sm)  # 第一次:STOPPED 落库 → STARTING → 撞端口回滚

        data = await get_instance(client, headers, uuid)
        assert data["status"] == "stopped"  # 关键:不是回退到 stopping
        events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=headers)).json()[
            "items"
        ]
        chain = [(e["from_status"], e["to_status"]) for e in events]
        assert ("stopping", "stopped") in chain
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
