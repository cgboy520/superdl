# 白盒用例:直探模块内部
# pyright: reportPrivateUsage=false
from datetime import timedelta
from typing import Any, cast

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.k8s.base import PodStatus
from app.core.k8s.fake import FakeOrchestrator
from app.core.outbox import OutboxTask
from app.core.timeutil import ensure_utc, now_utc
from app.modules.orchestrator.models import Instance, InstanceEvent, PortAllocation
from app.modules.orchestrator.reconciler import reconcile_once
from tests.helpers import (
    IMAGE_PYTORCH,
    admin_headers,
    create_instance_api,
    create_test_sku,
    create_user_with_key,
    drain,
    drain_strict,
    fund_wallet,
    funded_user,
    get_instance,
    make_instance,
    provision_running,
    register,
    seed_node_spec,
)

pytestmark = pytest.mark.usefixtures("fake")


class TestCreateLifecycle:
    async def test_idem_key_param_mismatch_409(self, client, sm, fake):
        """同键异参(改了 GPU 数):409,不返回上一台实例。"""
        headers, _user_id, key_id = await funded_user(client, sm, "13900000031")
        sku_id = await create_test_sku(sm)
        h = {**headers, "Idempotency-Key": "inst-idem-mix"}
        body = {
            "sku_id": sku_id,
            "gpu_count": 1,
            "image_ref": IMAGE_PYTORCH,
            "ssh_key_ids": [key_id],
        }
        a = await client.post("/api/v1/instances", json=body, headers=h)
        assert a.status_code == 202, a.text
        b = await client.post("/api/v1/instances", json={**body, "gpu_count": 2}, headers=h)
        assert b.status_code == 409
        assert b.json()["message_key"] == "common.idempotencyKeyMismatch"

    async def test_full_create_to_running(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession], fake: FakeOrchestrator
    ):
        headers, user_id, key_id = await create_user_with_key(client)
        await fund_wallet(sm, user_id)
        sku_id = await create_test_sku(sm)

        data = await create_instance_api(client, headers, sku_id, key_id)
        assert data["status"] == "creating"
        uuid = data["uuid"]

        # outbox worker 建 Pod(drain_strict 断言任务成功)
        assert await drain_strict(sm) == (1, 0)
        assert (f"tenant-{user_id}", uuid) in fake.pods
        data = await get_instance(client, headers, uuid)
        assert data["ssh_port"] is not None

        # Pod 未 Ready → 仍 creating
        await reconcile_once(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "creating"

        # Ready → running(计费开始)
        fake.mark_ready(f"tenant-{user_id}", uuid)
        counts = await reconcile_once(sm)
        assert counts["to_running"] == 1
        assert (await get_instance(client, headers, uuid))["status"] == "running"

        # 事件时间线 = 计费依据
        events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=headers)).json()[
            "items"
        ]  # 降序:items[0] 是最新事件
        assert [(e["from_status"], e["to_status"]) for e in events] == [
            ("creating", "running"),
            (None, "creating"),
        ]

        # jupyter_url 是一次性 bootstrap 票据(不含 token 本体)
        access = (await client.get(f"/api/v1/instances/{uuid}/access", headers=headers)).json()
        assert access["ssh_command"].startswith("ssh root@")
        assert uuid in access["jupyter_url"]
        assert "/superdl-bootstrap?" in access["jupyter_url"]
        assert "token=" not in access["jupyter_url"]
        # 票据签名以实例 token 为 HMAC 密钥;token 密文落库(enc:v2:)
        import hashlib
        import hmac
        from urllib.parse import parse_qs, urlparse

        from app.modules.orchestrator.service import _token_plain

        qs = parse_qs(urlparse(access["jupyter_url"]).query)
        async with sm() as session:
            inst = (
                await session.execute(select(Instance).where(Instance.uuid == uuid))
            ).scalar_one()
        assert inst.jupyter_token.startswith("enc:v2:")
        expected_sig = hmac.new(
            _token_plain(inst).encode(),
            f"{qs['code'][0]}.{qs['exp'][0]}".encode(),
            hashlib.sha256,
        ).hexdigest()
        assert qs["sig"][0] == expected_sig

    async def test_insufficient_balance(self, client, sm):
        headers, _user_id, key_id = await create_user_with_key(client)
        sku_id = await create_test_sku(sm)  # 未充值
        resp = await client.post(
            "/api/v1/instances",
            json={"sku_id": sku_id, "image_ref": "img", "ssh_key_ids": [key_id]},
            headers=headers,
        )
        assert resp.json()["code"] == "INSUFFICIENT_BALANCE"

    async def test_requires_ssh_key(self, client, sm):
        data = await register(client, "13900000002")
        headers = {"Authorization": f"Bearer {data['access_token']}"}
        await fund_wallet(sm, data["user"]["id"])
        sku_id = await create_test_sku(sm)
        resp = await client.post(
            "/api/v1/instances",
            json={"sku_id": sku_id, "image_ref": "img", "ssh_key_ids": [999]},
            headers=headers,
        )
        assert resp.json()["code"] == "SSH_KEY_INVALID"

    async def test_gpu_count_exceeds_sku_limit(self, client, sm):
        headers, user_id, key_id = await create_user_with_key(client)
        await fund_wallet(sm, user_id)
        sku_id = await create_test_sku(sm, max_gpus_per_instance=2)
        resp = await client.post(
            "/api/v1/instances",
            json={"sku_id": sku_id, "gpu_count": 4, "image_ref": "img", "ssh_key_ids": [key_id]},
            headers=headers,
        )
        assert resp.json()["code"] == "VALIDATION_ERROR"


class TestStopStartRestart:
    async def test_stop_then_start(self, client, sm, fake):
        headers, uuid, user_id = await provision_running(client, sm, fake)

        resp = await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        assert resp.json()["status"] == "stopping"
        await drain(sm)  # 删 Pod
        assert (f"tenant-{user_id}", uuid) not in fake.pods
        counts = await reconcile_once(sm)
        assert counts["to_stopped"] == 1
        assert (await get_instance(client, headers, uuid))["status"] == "stopped"

        # 端口保留(关机不释放端口)
        port_before = (await get_instance(client, headers, uuid))["ssh_port"]

        # 实例盘活过关机(具名 PVC 不随 Pod 走)
        disk_before = fake.instance_disks[(f"tenant-{user_id}", uuid)]

        resp = await client.post(f"/api/v1/instances/{uuid}/start", headers=headers)
        assert resp.json()["status"] == "starting"
        await drain(sm)
        fake.mark_ready(f"tenant-{user_id}", uuid)
        await reconcile_once(sm)
        data = await get_instance(client, headers, uuid)
        assert data["status"] == "running"
        assert data["ssh_port"] == port_before
        assert fake.instance_disks[(f"tenant-{user_id}", uuid)] == disk_before

    async def test_restart_waits_for_pod_to_actually_disappear(self, client, sm, fake):
        """重启等对象真正消失再同名重建;Fake 显式打开 graceful_delete。"""
        from app.core.outbox import OutboxTask

        headers, uuid, user_id = await provision_running(client, sm, fake)
        ns = f"tenant-{user_id}"
        fake.graceful_delete = True

        await client.post(f"/api/v1/instances/{uuid}/restart", headers=headers)
        await drain(sm)
        # 优雅期内实例留在 stopping,任务退避重试
        assert (await get_instance(client, headers, uuid))["status"] == "stopping"
        async with sm() as session:
            task = (
                await session.execute(
                    select(OutboxTask).where(OutboxTask.type == "instance.restart")
                )
            ).scalar_one()
            assert task.status == "pending" and task.retries == 1
            task.next_retry_at = now_utc()  # 快进退避
            await session.commit()

        # 优雅期结束 → 下一次重试续跑完整条链
        fake.finish_delete(ns, uuid)
        fake.graceful_delete = False
        await drain(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "starting"
        assert (ns, uuid) in fake.pods  # 新 Pod 真的建出来了
        fake.mark_ready(ns, uuid)
        await reconcile_once(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "running"
        # 每个边都留事件:stopping → stopped → starting → running
        events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=headers)).json()[
            "items"
        ]
        chain = [(e["from_status"], e["to_status"]) for e in events]
        assert ("running", "stopping") in chain
        assert ("stopping", "stopped") in chain
        assert ("stopped", "starting") in chain

    async def test_stop_requires_running(self, client, sm, fake):
        headers, uuid, _user_id = await provision_running(client, sm, fake)
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        resp = await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        assert resp.json()["code"] == "INSTANCE_INVALID_TRANSITION"


class TestFailureModes:
    async def test_pod_lost_marks_failed_and_stops_billing(self, client, sm, fake):
        """kill pod 后一轮 reconcile 内 DB 转 failed 并停止计费。"""
        headers, uuid, user_id = await provision_running(client, sm, fake)
        fake.kill_pod(f"tenant-{user_id}", uuid)
        counts = await reconcile_once(sm)
        assert counts["to_failed"] == 1
        data = await get_instance(client, headers, uuid)
        assert data["status"] == "failed"
        # running→failed 事件在案(结算据此停费)
        events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=headers)).json()[
            "items"
        ]  # 降序:items[0] 是最新事件
        assert events[0]["from_status"] == "running"
        assert events[0]["to_status"] == "failed"
        # 盘不动(pod_lost 不是终结)
        assert (f"tenant-{user_id}", uuid) in fake.instance_disks
        # 端口已回收
        async with sm() as session:
            ports = (await session.execute(select(PortAllocation.instance_id))).scalars().all()
        assert all(p is None for p in ports)

    async def test_creating_timeout_fails_and_cleans(self, client, sm, fake):
        headers, user_id, key_id = await funded_user(client, sm, "13900000021")
        sku_id = await create_test_sku(sm)
        data = await create_instance_api(client, headers, sku_id, key_id)
        uuid = data["uuid"]
        await drain(sm)  # Pod 已建但永不 Ready(auto_ready=False)

        # 「进入 creating」事件时刻回拨超过 5 分钟超时线(超时基准是事件时刻,不是 updated_at)
        async with sm() as session:
            inst_id = (
                await session.execute(select(Instance.id).where(Instance.uuid == uuid))
            ).scalar_one()
            await session.execute(
                update(InstanceEvent)
                .where(InstanceEvent.instance_id == inst_id)
                .values(created_at=now_utc() - timedelta(minutes=6))
            )
            await session.execute(
                update(Instance).where(Instance.uuid == uuid).values(updated_at=now_utc())
            )
            await session.commit()

        counts = await reconcile_once(sm)
        assert counts["to_failed"] == 1
        assert (await get_instance(client, headers, uuid))["status"] == "failed"
        assert (f"tenant-{user_id}", uuid) not in fake.pods  # 已清理
        # 首开失败:盘一并回收(Pod 未消失时经 outbox disk_cleanup 延迟回收)
        await drain(sm)
        await reconcile_once(sm)
        assert (f"tenant-{user_id}", uuid) not in fake.instance_disks
        # 创建失败通知用户
        notes = (await client.get("/api/v1/notifications", headers=headers)).json()["items"]
        assert any("调度超时" in n["title"] for n in notes)

    async def test_leaked_pod_reclaimed(self, client, sm, fake):
        """DB 无主的泄漏 Pod 被回收。"""
        headers, uuid, user_id = await provision_running(client, sm, fake)
        ns = f"tenant-{user_id}"
        leaked_spec = fake.pods[(ns, uuid)].spec
        fake.inject_leaked_pod(ns, "deadbeef" * 4, leaked_spec)
        counts = await reconcile_once(sm)
        assert counts["leaked"] == 1
        assert (ns, "deadbeef" * 4) not in fake.pods
        # 正常实例不受影响
        assert (await get_instance(client, headers, uuid))["status"] == "running"


class _Clock:
    """可推进的假时钟(注入 reconciler 的 now_utc)。"""

    def __init__(self) -> None:
        self.offset = timedelta()

    def __call__(self):
        return now_utc() + self.offset


class TestUnreadyTimer:
    """not-ready 计时器跨轮次累积(reconciler.unready_since);
    挂了 = 超时分支成死代码,失联实例永远计费。"""

    @staticmethod
    async def _unready_since(sm, uuid: str):
        async with sm() as session:
            return (
                await session.execute(select(Instance.unready_since).where(Instance.uuid == uuid))
            ).scalar_one()

    @staticmethod
    def _pin(monkeypatch, clock: _Clock) -> None:
        """宽限窗钉死 600s,时钟由用例推进。"""
        from app.core.config import get_settings
        from app.modules.orchestrator import reconciler as reconciler_mod

        monkeypatch.setattr(get_settings(), "running_unready_timeout_seconds", 600)
        monkeypatch.setattr(reconciler_mod, "now_utc", clock)

    async def test_timer_accumulates_across_rounds_then_fails(self, client, sm, fake, monkeypatch):
        """两轮巡检跨过宽限窗即判失联:第一轮起表、第二轮到点。"""
        headers, uuid, user_id = await provision_running(client, sm, fake, phone="13900000045")
        ns = f"tenant-{user_id}"
        clock = _Clock()
        self._pin(monkeypatch, clock)

        # 第一轮:起表不判故障
        fake.mark_unready(ns, uuid)
        assert (await reconcile_once(sm))["to_failed"] == 0
        first_seen = await self._unready_since(sm, uuid)
        assert first_seen is not None  # 计时器必须留在库里,下一轮才有得比

        # 第二轮:持续到宽限窗外 → 判失联
        clock.offset = timedelta(seconds=601)
        assert (await reconcile_once(sm))["to_failed"] == 1
        assert (await get_instance(client, headers, uuid))["status"] == "failed"

        events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=headers)).json()[
            "items"
        ]  # 降序:items[0] 是最新事件
        assert (events[0]["from_status"], events[0]["to_status"]) == ("running", "failed")
        assert events[0]["reason"] == "node_lost"
        # 首次不就绪时刻进事件 metadata
        assert events[0]["event_metadata"]["unready_since"] == ensure_utc(first_seen).isoformat()
        # 强删:grace=0
        assert (ns, uuid) not in fake.pods
        # 失联通知用户
        notes = (await client.get("/api/v1/notifications", headers=headers)).json()["items"]
        assert any("节点失联" in n["title"] for n in notes)

    async def test_recovery_restarts_the_timer(self, client, sm, fake, monkeypatch):
        """抖动恢复重新计时:ready 那轮清表,再次不就绪从零起算。"""
        headers, uuid, user_id = await provision_running(client, sm, fake, phone="13900000046")
        ns = f"tenant-{user_id}"
        clock = _Clock()
        self._pin(monkeypatch, clock)

        fake.mark_unready(ns, uuid)
        await reconcile_once(sm)
        first_seen = await self._unready_since(sm, uuid)
        assert first_seen is not None

        # 恢复 → 清表
        fake.mark_ready(ns, uuid)
        clock.offset = timedelta(seconds=300)
        assert (await reconcile_once(sm))["to_failed"] == 0
        assert await self._unready_since(sm, uuid) is None

        # 再次不就绪:计时从这一轮重新起算 → 不判故障
        fake.mark_unready(ns, uuid)
        clock.offset = timedelta(seconds=660)
        assert (await reconcile_once(sm))["to_failed"] == 0
        restarted = await self._unready_since(sm, uuid)
        assert restarted is not None
        assert ensure_utc(restarted) - ensure_utc(first_seen) > timedelta(seconds=600)
        assert (await get_instance(client, headers, uuid))["status"] == "running"

        # 新一轮计时到点才判故障,截断依据是重启后的时刻
        clock.offset = timedelta(seconds=1262)
        assert (await reconcile_once(sm))["to_failed"] == 1
        events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=headers)).json()[
            "items"
        ]
        assert events[0]["event_metadata"]["unready_since"] == ensure_utc(restarted).isoformat()


class TestRelease:
    async def test_release_flow_and_port_reuse(self, client, sm, fake):
        headers, uuid, user_id = await provision_running(client, sm, fake)
        # 关机
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        assert (f"tenant-{user_id}", uuid) in fake.instance_disks  # 关机后盘还在
        port = (await get_instance(client, headers, uuid))["ssh_port"]

        # 释放
        resp = await client.delete(f"/api/v1/instances/{uuid}", headers=headers)
        assert resp.json()["status"] == "releasing"
        await drain(sm)
        counts = await reconcile_once(sm)
        assert counts["to_released"] == 1

        # released 实例不出现在列表
        instances = (await client.get("/api/v1/instances", headers=headers)).json()["items"]
        assert uuid not in [i["uuid"] for i in instances]

        # 盘销毁(instance.disk_cleanup outbox,drain 后终态)
        await drain(sm)
        events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=headers)).json()[
            "items"
        ]  # 降序:items[0] 是最新事件
        assert events[0]["to_status"] == "released"
        assert (f"tenant-{user_id}", uuid) not in fake.instance_disks

        # 端口回池
        async with sm() as session:
            row = (
                await session.execute(select(PortAllocation).where(PortAllocation.port == port))
            ).scalar_one()
            assert row.instance_id is None

    async def test_release_requires_stopped(self, client, sm, fake):
        headers, uuid, _user_id = await provision_running(client, sm, fake)
        resp = await client.delete(f"/api/v1/instances/{uuid}", headers=headers)
        assert resp.json()["code"] == "INSTANCE_NOT_STOPPED"

    async def test_release_is_idempotent(self, client, sm, fake):
        """重复 DELETE:releasing/released 态回当前状态,不报 400。"""
        headers, uuid, _user_id = await provision_running(client, sm, fake)
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)

        resp = await client.delete(f"/api/v1/instances/{uuid}", headers=headers)
        assert resp.json()["status"] == "releasing"
        # 重试/双击回当前状态
        resp = await client.delete(f"/api/v1/instances/{uuid}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "releasing"
        await drain(sm)
        await reconcile_once(sm)
        resp = await client.delete(f"/api/v1/instances/{uuid}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "released"

    async def test_events_pagination_desc(self, client, sm, fake):
        """事件时间线:降序(最新在前)+ 游标翻页覆盖全量、不重不漏。"""
        headers, uuid, _user_id = await provision_running(client, sm, fake)
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)

        page1 = (
            await client.get(
                f"/api/v1/instances/{uuid}/events", params={"limit": 2}, headers=headers
            )
        ).json()
        assert len(page1["items"]) == 2
        assert page1["next_cursor"] is not None
        page2 = (
            await client.get(
                f"/api/v1/instances/{uuid}/events",
                params={"limit": 2, "cursor": page1["next_cursor"]},
                headers=headers,
            )
        ).json()
        ids = [e["id"] for e in page1["items"] + page2["items"]]
        assert ids == sorted(ids, reverse=True)  # 全局降序
        assert page2["next_cursor"] is None
        # 事件总数 4 条(creating→running→stopping→stopped)
        assert len(ids) == 4

    async def test_release_failed_instance_leaves_list(self, client, sm, fake):
        """失败实例可被释放并出清列表。"""
        headers, uuid, user_id = await provision_running(client, sm, fake)
        fake.kill_pod(f"tenant-{user_id}", uuid)  # 故障 → failed
        await reconcile_once(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "failed"

        resp = await client.delete(f"/api/v1/instances/{uuid}", headers=headers)
        assert resp.json()["status"] == "releasing"
        await drain(sm)
        counts = await reconcile_once(sm)
        assert counts["to_released"] == 1

        instances = (await client.get("/api/v1/instances", headers=headers)).json()["items"]
        assert uuid not in [i["uuid"] for i in instances]
        events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=headers)).json()[
            "items"
        ]  # 降序:items[0] 是最新事件
        assert [e["to_status"] for e in events][:3] == ["released", "releasing", "failed"]

    async def test_cancel_creating_instance(self, client, sm, fake):
        """creating 可被用户主动取消,零扣费。"""
        headers, _user_id, key_id = await funded_user(client, sm, "13900000041")
        sku_id = await create_test_sku(sm)
        data = await create_instance_api(client, headers, sku_id, key_id)
        uuid = data["uuid"]
        assert data["status"] == "creating"  # 未 mark_ready,停在 creating

        resp = await client.delete(f"/api/v1/instances/{uuid}", headers=headers)
        assert resp.json()["status"] == "releasing"
        await drain(sm)
        counts = await reconcile_once(sm)
        assert counts["to_released"] == 1

        # 列表不再出现;事件链 creating → releasing → released,从未进入 running
        instances = (await client.get("/api/v1/instances", headers=headers)).json()["items"]
        assert uuid not in [i["uuid"] for i in instances]
        events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=headers)).json()[
            "items"
        ]  # 降序:items[0] 是最新事件
        assert events[0]["to_status"] == "released"
        assert "running" not in [e["to_status"] for e in events]


class TestPortPool:
    async def test_pool_exhaustion(self, client, sm, fake, monkeypatch):
        from app.core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "ssh_port_range_start", 31000)
        monkeypatch.setattr(settings, "ssh_port_range_end", 31000)  # 池容量 1

        headers, _user_id, key_id = await funded_user(client, sm, "13900000041", "500.00")
        sku_id = await create_test_sku(sm)
        a = await create_instance_api(client, headers, sku_id, key_id)
        await drain(sm)
        assert (await get_instance(client, headers, a["uuid"]))["ssh_port"] == 31000

        b = await create_instance_api(client, headers, sku_id, key_id)
        await drain(sm)  # 第二台分配端口失败 → 任务重试;实例仍 creating
        data = await get_instance(client, headers, b["uuid"])
        assert data["ssh_port"] is None

    async def test_excluded_port_is_skipped(self, client, sm, fake, monkeypatch):
        """已知被占用的 NodePort 不分配(分配段内随机,只断言不落 excluded、不低于 start)。"""
        from app.core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "ssh_port_range_start", 31500)
        monkeypatch.setattr(settings, "ssh_port_excluded", {31500, 31501})

        headers, _user_id, key_id = await funded_user(client, sm, "13900000042", "500.00")
        sku_id = await create_test_sku(sm)
        data = await create_instance_api(client, headers, sku_id, key_id)
        await drain(sm)
        port = (await get_instance(client, headers, data["uuid"]))["ssh_port"]
        assert port not in (31500, 31501) and 31500 < port <= 32767

    async def test_taken_node_port_is_blocked_and_recovered(self, client, sm, fake, monkeypatch):
        """撞上被占 NodePort 后自愈:端口标 blocked 并换一个重试(首个分出端口动态拦截)。"""
        from app.core.config import get_settings
        from app.core.k8s import NodePortTaken

        settings = get_settings()
        monkeypatch.setattr(settings, "ssh_port_range_start", 31800)
        monkeypatch.setattr(settings, "ssh_port_excluded", set())

        taken: set[int] = set()
        original = fake.create_instance

        async def guarded(spec):
            # 首个分出的端口只撞这一次,之后放行
            if not taken:
                taken.add(spec.ssh_node_port)
                raise NodePortTaken(spec.ssh_node_port)
            await original(spec)

        monkeypatch.setattr(fake, "create_instance", guarded)

        headers, _user_id, key_id = await funded_user(client, sm, "13900000043", "500.00")
        sku_id = await create_test_sku(sm)
        data = await create_instance_api(client, headers, sku_id, key_id)
        await drain(sm)

        # 端口标 blocked(独立事务)
        assert len(taken) == 1
        taken_port = next(iter(taken))
        async with sm() as session:
            row = (
                await session.execute(
                    select(PortAllocation).where(PortAllocation.port == taken_port)
                )
            ).scalar_one()
        assert row.blocked is True and row.instance_id is None

        # 重试绕开被标记端口
        async with sm() as session:
            await session.execute(
                update(OutboxTask)
                .where(OutboxTask.type == "instance.create")
                .values(next_retry_at=now_utc())
            )
            await session.commit()
        await drain(sm)
        port = (await get_instance(client, headers, data["uuid"]))["ssh_port"]
        assert port is not None and port != taken_port


class TestAdminOps:
    async def test_admin_list_and_force_stop(self, client, sm, fake):
        headers, uuid, _user_id = await provision_running(client, sm, fake)
        ah = await admin_headers(sm, client, role="ops")

        listed = (await client.get("/api/admin/v1/instances", headers=ah)).json()["items"]
        assert uuid in [i["uuid"] for i in listed]

        resp = await client.post(
            f"/api/admin/v1/instances/{uuid}/force-stop",
            json={"reason": "违规用途排查"},
            headers=ah,
        )
        assert resp.json()["status"] == "stopping"
        events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=headers)).json()[
            "items"
        ]  # 降序:items[0] 是最新事件
        assert events[0]["actor"] == "admin"
        assert events[0]["event_metadata"]["admin_reason"] == "违规用途排查"

    async def test_frozen_cannot_start(self, client, sm, fake):
        headers, uuid, _user_id = await provision_running(client, sm, fake)
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        async with sm() as session:
            await session.execute(
                update(Instance).where(Instance.uuid == uuid).values(status="frozen")
            )
            await session.commit()
        resp = await client.post(f"/api/v1/instances/{uuid}/start", headers=headers)
        assert resp.json()["code"] == "INSTANCE_FROZEN"


class TestInventoryProvider:
    async def test_market_inventory_reflects_fake_capacity(self, client, sm, fake):
        """市场库存按 (池, canonical 型号) 从节点台账估算。"""
        await create_test_sku(sm)  # hami 池,50% 算力,超卖 1.5
        await seed_node_spec(sm)  # 台账:hami 池 32 张 RTX4090 全空闲
        skus = (await client.get("/api/v1/skus")).json()
        # 32 卡空闲 × (100×1.5/50)=3 实例/卡 = 96
        assert skus[0]["available_count"] == 96

    async def test_market_inventory_excludes_not_ready_nodes(self, client, sm, fake):
        """NotReady / Cordoned 节点的卡不计入可售库存。"""
        await create_test_sku(sm)
        await seed_node_spec(sm, node_name="nr", status="NotReady")
        skus = (await client.get("/api/v1/skus")).json()
        assert skus[0]["available_count"] == 0


class TestImageRefValidation:
    def test_digest_pinned_ref_accepted(self):
        """镜像形态校验同时收 tag + digest。"""
        from app.core.registry import is_valid_image_ref

        d = "a" * 64
        assert is_valid_image_ref(
            f"harbor.example.com/superdl/pytorch:2.13.0-cu132-py313@sha256:{d}"
        )
        assert is_valid_image_ref(f"harbor.example.com:10031/superdl/pytorch:2.13.0@sha256:{d}")
        assert is_valid_image_ref(f"harbor.example.com/superdl/pytorch@sha256:{d}")  # 纯 digest
        assert is_valid_image_ref("harbor.example.com/superdl/pytorch:2.13.0-cu132-py313")  # 纯 tag
        # 长度不足、大写十六进制、算法名写错都拒
        assert not is_valid_image_ref(f"harbor.example.com/superdl/pytorch:t@sha256:{'a' * 63}")
        assert not is_valid_image_ref(f"harbor.example.com/superdl/pytorch:t@sha256:{'A' * 64}")
        assert not is_valid_image_ref(f"harbor.example.com/superdl/pytorch:t@sha512:{d}")

    def test_admin_image_schema_rejects_malformed_ref(self):
        """管理端写入路径同样校验 ref 形态。"""
        import pytest as _pytest
        from pydantic import ValidationError

        from app.modules.catalog.schemas import ImageCreate, ImageUpdate

        d = "a" * 64
        ok = ImageCreate(
            framework="PyTorch",
            framework_version="2.13.0",
            python_version="3.13",
            cuda_version="13.2",
            image_ref=f" harbor.example.com/superdl/pytorch:2.13.0@sha256:{d} ",
        )
        assert ok.image_ref.endswith(d) and not ok.image_ref.startswith(" ")  # 顺带去空白
        with _pytest.raises(ValidationError):
            ImageCreate(
                framework="PyTorch",
                framework_version="2.13.0",
                python_version="3.13",
                cuda_version="13.2",
                image_ref="not a valid ref!",
            )
        with _pytest.raises(ValidationError):
            ImageUpdate(image_ref=f"harbor.example.com/superdl/pytorch:t@sha256:{'a' * 63}")
        assert ImageUpdate(image_ref=None).image_ref is None  # 不传不校验

    async def test_malformed_image_ref_rejected(self, client, sm):
        headers, _user_id, key_id = await funded_user(client, sm, "13500000090")
        sku_id = await create_test_sku(sm)
        resp = await client.post(
            "/api/v1/instances",
            json={
                "sku_id": sku_id,
                "image_ref": "not a valid ref!",
                "ssh_key_ids": [key_id],
            },
            headers=headers,
        )
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "orchestrator.imageRefInvalid"

    async def test_registry_allowlist_blocks_foreign_registry(self, client, sm, monkeypatch):
        from app.core.config import get_settings

        headers, _user_id, key_id = await funded_user(client, sm, "13500000091")
        sku_id = await create_test_sku(sm)
        settings = get_settings()
        monkeypatch.setattr(
            settings, "image_allowed_registries", "registry.superdl.local/", raising=False
        )
        resp = await client.post(
            "/api/v1/instances",
            json={
                "sku_id": sku_id,
                "image_ref": "evil.example.com/miner:latest",
                "ssh_key_ids": [key_id],
            },
            headers=headers,
        )
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "orchestrator.imageRefNotAllowed"
        # 白名单前缀内的镜像照常放行
        resp = await client.post(
            "/api/v1/instances",
            json={
                "sku_id": sku_id,
                "image_ref": IMAGE_PYTORCH,
                "ssh_key_ids": [key_id],
            },
            headers=headers,
        )
        assert resp.status_code == 202, resp.text


class TestServiceWorkloadUnreadyExemption:
    """服务型实例持续 not-ready 不判故障(reconciler._running_pod_lost_reason);
    节点失联不豁免。"""

    @staticmethod
    async def _reason(
        workload_type: str, st: PodStatus, *, node_not_ready: bool | None
    ) -> str | None:
        from app.modules.orchestrator.reconciler import _running_pod_lost_reason

        class _Session:
            async def flush(self) -> None: ...

        return await _running_pod_lost_reason(
            cast(Any, _Session()),
            make_instance(
                uuid="u1",
                spec={},
                image_ref="img",
                status="running",
                jupyter_token="enc:v2:x",
                workload_type=workload_type,
                # 已超过传入的宽限窗
                unready_since=now_utc() - timedelta(hours=1),
            ),
            st,
            timedelta(minutes=5),
            node_not_ready,
        )

    _UNREADY = PodStatus(exists=True, ready=False, phase="Running", node_name="n1")

    async def test_dev_unready_on_healthy_node_fails(self):
        reason = await self._reason("dev", self._UNREADY, node_not_ready=False)
        assert reason == "pod_unready"

    async def test_service_unready_on_healthy_node_survives(self):
        reason = await self._reason("service", self._UNREADY, node_not_ready=False)
        assert reason is None

    async def test_service_still_fails_when_node_lost(self):
        """节点真失联时不豁免。"""
        reason = await self._reason("service", self._UNREADY, node_not_ready=True)
        assert reason == "node_lost"

    async def test_service_still_fails_when_pod_gone(self):
        reason = await self._reason("service", PodStatus(exists=False), node_not_ready=False)
        assert reason == "pod_lost"

    async def test_service_still_fails_when_pod_evicted(self):
        """running 态的删除一定是外部发起(被驱逐/外部删除)。"""
        reason = await self._reason(
            "service",
            PodStatus(exists=True, ready=False, phase="Running", deleting=True),
            node_not_ready=False,
        )
        assert reason == "pod_lost"
