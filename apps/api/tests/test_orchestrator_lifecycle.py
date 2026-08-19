from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.k8s import set_orchestrator
from app.core.k8s.fake import FakeOrchestrator
from app.core.outbox import drain
from app.core.timeutil import now_utc
from app.modules.orchestrator.models import Instance, PortAllocation
from app.modules.orchestrator.reconciler import reconcile_once
from tests.helpers import create_test_sku, create_user_with_key, fund_wallet

pytestmark = pytest.mark.usefixtures("fake")


@pytest.fixture
def fake():
    orch = FakeOrchestrator(auto_ready=False)
    set_orchestrator(orch)
    yield orch
    set_orchestrator(None)


async def create_instance_api(
    client: AsyncClient,
    headers: dict[str, str],
    sku_id: int,
    key_id: int,
    *,
    gpu_count: int = 1,
    idem: str | None = None,
) -> dict:
    h = dict(headers)
    if idem:
        h["Idempotency-Key"] = idem
    resp = await client.post(
        "/api/v1/instances",
        json={
            "sku_id": sku_id,
            "gpu_count": gpu_count,
            "image_ref": "registry.superdl.local/pytorch:2.9.0-cu128",
            "ssh_key_ids": [key_id],
        },
        headers=h,
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


async def get_instance(client: AsyncClient, headers: dict, uuid: str) -> dict:
    resp = await client.get(f"/api/v1/instances/{uuid}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestCreateLifecycle:
    async def test_full_create_to_running(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession], fake: FakeOrchestrator
    ):
        headers, user_id, key_id = await create_user_with_key(client)
        await fund_wallet(sm, user_id)
        sku_id = await create_test_sku(sm)

        data = await create_instance_api(client, headers, sku_id, key_id)
        assert data["status"] == "creating"
        uuid = data["uuid"]

        # outbox worker 建 Pod
        assert await drain(sm) == 1
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
        events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=headers)).json()
        assert [(e["from_status"], e["to_status"]) for e in events] == [
            (None, "creating"),
            ("creating", "running"),
        ]

        # 接入信息
        access = (await client.get(f"/api/v1/instances/{uuid}/access", headers=headers)).json()
        assert access["ssh_command"].startswith("ssh root@")
        assert uuid in access["jupyter_url"]
        assert "token=" in access["jupyter_url"]

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
        from tests.test_account_auth import register

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

    async def test_idempotency_key(self, client, sm):
        headers, user_id, key_id = await create_user_with_key(client)
        await fund_wallet(sm, user_id)
        sku_id = await create_test_sku(sm)
        a = await create_instance_api(client, headers, sku_id, key_id, idem="idem-1")
        b = await create_instance_api(client, headers, sku_id, key_id, idem="idem-1")
        assert a["uuid"] == b["uuid"]
        instances = (await client.get("/api/v1/instances", headers=headers)).json()
        assert len(instances) == 1

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


async def _provision_running(client, sm, fake, phone="13900000010") -> tuple[dict, str, int]:
    """建好一台 running 实例。返回 (headers, uuid, user_id)。"""
    headers, user_id, key_id = await create_user_with_key(client, phone)
    await fund_wallet(sm, user_id)
    sku_id = await create_test_sku(sm)
    data = await create_instance_api(client, headers, sku_id, key_id)
    await drain(sm)
    fake.mark_ready(f"tenant-{user_id}", data["uuid"])
    await reconcile_once(sm)
    return headers, data["uuid"], user_id


class TestStopStartRestart:
    async def test_stop_then_start(self, client, sm, fake):
        headers, uuid, user_id = await _provision_running(client, sm, fake)

        resp = await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        assert resp.json()["status"] == "stopping"
        await drain(sm)  # 删 Pod
        assert (f"tenant-{user_id}", uuid) not in fake.pods
        counts = await reconcile_once(sm)
        assert counts["to_stopped"] == 1
        assert (await get_instance(client, headers, uuid))["status"] == "stopped"

        # 端口保留(关机不释放端口)
        port_before = (await get_instance(client, headers, uuid))["ssh_port"]

        resp = await client.post(f"/api/v1/instances/{uuid}/start", headers=headers)
        assert resp.json()["status"] == "starting"
        await drain(sm)
        fake.mark_ready(f"tenant-{user_id}", uuid)
        await reconcile_once(sm)
        data = await get_instance(client, headers, uuid)
        assert data["status"] == "running"
        assert data["ssh_port"] == port_before

    async def test_stop_requires_running(self, client, sm, fake):
        headers, uuid, _user_id = await _provision_running(client, sm, fake)
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        resp = await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        assert resp.json()["code"] == "INSTANCE_INVALID_TRANSITION"

    async def test_restart_full_chain(self, client, sm, fake):
        headers, uuid, user_id = await _provision_running(client, sm, fake)
        resp = await client.post(f"/api/v1/instances/{uuid}/restart", headers=headers)
        assert resp.json()["status"] == "stopping"
        await drain(sm)  # handler: 删 Pod → stopped → starting → 建 Pod
        data = await get_instance(client, headers, uuid)
        assert data["status"] == "starting"
        fake.mark_ready(f"tenant-{user_id}", uuid)
        await reconcile_once(sm)
        assert (await get_instance(client, headers, uuid))["status"] == "running"

        events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=headers)).json()
        chain = [(e["from_status"], e["to_status"]) for e in events]
        assert ("running", "stopping") in chain
        assert ("stopping", "stopped") in chain
        assert ("stopped", "starting") in chain


class TestFailureModes:
    async def test_pod_lost_marks_failed_and_stops_billing(self, client, sm, fake):
        """验收:kill pod 后(一轮 reconcile 内)DB 转 failed 并停止计费。"""
        headers, uuid, user_id = await _provision_running(client, sm, fake)
        fake.kill_pod(f"tenant-{user_id}", uuid)
        counts = await reconcile_once(sm)
        assert counts["to_failed"] == 1
        data = await get_instance(client, headers, uuid)
        assert data["status"] == "failed"
        # 计费边:running→failed 事件在案(结算按此停费)
        events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=headers)).json()
        assert events[-1]["from_status"] == "running"
        assert events[-1]["to_status"] == "failed"
        # 端口已回收
        async with sm() as session:
            ports = (await session.execute(select(PortAllocation.instance_id))).scalars().all()
        assert all(p is None for p in ports)

    async def test_creating_timeout_fails_and_cleans(self, client, sm, fake):
        headers, user_id, key_id = await create_user_with_key(client, "13900000021")
        await fund_wallet(sm, user_id)
        sku_id = await create_test_sku(sm)
        data = await create_instance_api(client, headers, sku_id, key_id)
        uuid = data["uuid"]
        await drain(sm)  # Pod 已建但永不 Ready(auto_ready=False)

        # 回拨 updated_at 超过 5 分钟超时线
        async with sm() as session:
            await session.execute(
                update(Instance)
                .where(Instance.uuid == uuid)
                .values(updated_at=now_utc() - timedelta(minutes=6))
            )
            await session.commit()

        counts = await reconcile_once(sm)
        assert counts["to_failed"] == 1
        assert (await get_instance(client, headers, uuid))["status"] == "failed"
        assert (f"tenant-{user_id}", uuid) not in fake.pods  # 已清理

    async def test_leaked_pod_reclaimed(self, client, sm, fake):
        """验收:DB 无主的泄漏 Pod 被回收(泄漏=白送算力)。"""
        headers, uuid, user_id = await _provision_running(client, sm, fake)
        ns = f"tenant-{user_id}"
        leaked_spec = fake.pods[(ns, uuid)].spec
        fake.inject_leaked_pod(ns, "deadbeef" * 4, leaked_spec)
        counts = await reconcile_once(sm)
        assert counts["leaked"] == 1
        assert (ns, "deadbeef" * 4) not in fake.pods
        # 正常实例不受影响
        assert (await get_instance(client, headers, uuid))["status"] == "running"


class TestRelease:
    async def test_release_flow_and_port_reuse(self, client, sm, fake):
        headers, uuid, user_id = await _provision_running(client, sm, fake)
        # 关机
        await client.post(f"/api/v1/instances/{uuid}/stop", headers=headers)
        await drain(sm)
        await reconcile_once(sm)
        port = (await get_instance(client, headers, uuid))["ssh_port"]

        # 释放
        resp = await client.delete(f"/api/v1/instances/{uuid}", headers=headers)
        assert resp.json()["status"] == "releasing"
        await drain(sm)
        counts = await reconcile_once(sm)
        assert counts["to_released"] == 1

        # released 实例不出现在列表
        instances = (await client.get("/api/v1/instances", headers=headers)).json()
        assert uuid not in [i["uuid"] for i in instances]

        # 事件含擦盘标记
        events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=headers)).json()
        assert events[-1]["to_status"] == "released"
        assert events[-1]["event_metadata"]["disk_wipe"] == "blkdiscard"

        # 端口回池并被下一实例复用
        _, _, key2 = await create_user_with_key(client, "13900000031")
        async with sm() as session:
            row = (
                await session.execute(select(PortAllocation).where(PortAllocation.port == port))
            ).scalar_one()
            assert row.instance_id is None

    async def test_release_requires_stopped(self, client, sm, fake):
        headers, uuid, _user_id = await _provision_running(client, sm, fake)
        resp = await client.delete(f"/api/v1/instances/{uuid}", headers=headers)
        assert resp.json()["code"] == "INSTANCE_NOT_STOPPED"


class TestPortPool:
    async def test_pool_exhaustion(self, client, sm, fake, monkeypatch):
        from app.core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "ssh_port_range_start", 31000)
        monkeypatch.setattr(settings, "ssh_port_range_end", 31000)  # 池容量 1

        headers, user_id, key_id = await create_user_with_key(client, "13900000041")
        await fund_wallet(sm, user_id, "500.00")
        sku_id = await create_test_sku(sm)
        a = await create_instance_api(client, headers, sku_id, key_id)
        await drain(sm)
        assert (await get_instance(client, headers, a["uuid"]))["ssh_port"] == 31000

        b = await create_instance_api(client, headers, sku_id, key_id)
        await drain(sm)  # 第二台分配端口失败 → 任务重试;实例仍 creating
        data = await get_instance(client, headers, b["uuid"])
        assert data["ssh_port"] is None


class TestAdminOps:
    async def test_admin_list_and_force_stop(self, client, sm, fake):
        from tests.test_catalog import admin_headers

        headers, uuid, _user_id = await _provision_running(client, sm, fake)
        ah = await admin_headers(sm, client, role="ops")

        listed = (await client.get("/api/admin/v1/instances", headers=ah)).json()
        assert uuid in [i["uuid"] for i in listed]

        resp = await client.post(
            f"/api/admin/v1/instances/{uuid}/force-stop",
            json={"reason": "违规用途排查"},
            headers=ah,
        )
        assert resp.json()["status"] == "stopping"
        events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=headers)).json()
        assert events[-1]["actor"] == "admin"
        assert events[-1]["event_metadata"]["admin_reason"] == "违规用途排查"

    async def test_frozen_cannot_start(self, client, sm, fake):
        headers, uuid, _user_id = await _provision_running(client, sm, fake)
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
        """orchestrator 的容量估算已注册为 catalog 库存 provider。"""
        from app.modules.catalog import inventory

        inventory.clear_cache()
        await create_test_sku(sm)  # hami 池,50% 算力,超卖 1.5
        skus = (await client.get("/api/v1/skus")).json()
        # 32 卡空闲 × (100×1.5/50)=3 实例/卡 = 96
        assert skus[0]["available_count"] == 96

    async def test_shared_price_decimal(self, client, sm, fake):
        headers, user_id, key_id = await create_user_with_key(client, "13900000051")
        await fund_wallet(sm, user_id, "3.36")  # 恰好 2 小时 1.68
        sku_id = await create_test_sku(sm, price_hourly=Decimal("1.6800"))
        data = await create_instance_api(client, headers, sku_id, key_id)
        assert data["price_hourly"] == "1.6800"
