"""E2E 演练:一条脚本跑通全生命周期(FakeOrchestrator + mock 支付)。

注册 → 充值 → 建数据盘 → 买共享档实例(挂盘)→ SSH/Jupyter 接入 → 停机(尾账)→
账单与事件时间线 → 释放(擦盘)→ 数据盘保留 → 全程资金自洽(充值 - 消费 = 余额)。
实机集群版同流程复用本脚本骨架(人工事项 #8)。
"""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.k8s import set_orchestrator
from app.core.k8s.fake import FakeOrchestrator
from app.core.outbox import drain
from app.modules.billing.models import BalanceLedger
from app.modules.orchestrator.reconciler import reconcile_once
from tests.helpers import create_test_sku, gen_ed25519_key


@pytest.fixture
def fake():
    orch = FakeOrchestrator(auto_ready=False)
    set_orchestrator(orch)
    yield orch
    set_orchestrator(None)


async def test_full_lifecycle_drill(client, sm, fake):
    # ── 1. 注册 ────────────────────────────────────────────────
    phone = "13411112222"
    assert (
        await client.post("/api/v1/auth/sms-code", json={"phone": phone, "purpose": "register"})
    ).status_code == 204
    reg = await client.post(
        "/api/v1/auth/register", json={"phone": phone, "sms_code": "123456", "accept_terms": True}
    )
    assert reg.status_code == 201
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    user_id = reg.json()["user"]["id"]

    # ── 2. 充值(mock 渠道 + 回调)────────────────────────────
    order = (
        await client.post(
            "/api/v1/wallet/recharges", json={"amount": "200.00", "channel": "mock"}, headers=h
        )
    ).json()
    assert (
        await client.post(
            "/api/v1/webhooks/mock",
            json={"order_no": order["order_no"], "amount": "200.00"},
        )
    ).status_code == 200
    assert (await client.get("/api/v1/wallet", headers=h)).json()["balance"] == "200.00"

    # ── 3. SSH 公钥 + 数据盘 ────────────────────────────────
    key = (
        await client.post(
            "/api/v1/ssh-keys",
            json={"name": "drill", "public_key": gen_ed25519_key()},
            headers=h,
        )
    ).json()
    disk = (
        await client.post("/api/v1/disks", json={"name": "drill-data", "size_gb": 100}, headers=h)
    ).json()

    # ── 4. 市场选共享档 → 创建实例(挂盘,Idempotency-Key)──
    sku_id = await create_test_sku(sm)  # 共享标准档 1.68/时 hami 池
    market = (await client.get("/api/v1/skus")).json()
    assert any(s["id"] == sku_id and s["available_count"] > 0 for s in market)

    inst = (
        await client.post(
            "/api/v1/instances",
            json={
                "sku_id": sku_id,
                "image_ref": "registry.superdl.local/pytorch:2.9.0-cu128",
                "ssh_key_ids": [key["id"]],
                "data_disk_id": disk["id"],
            },
            headers={**h, "Idempotency-Key": "drill-1"},
        )
    ).json()
    uuid = inst["uuid"]
    assert inst["status"] == "creating"

    # outbox 建 Pod → Ready → reconciler 计费开始
    await drain(sm)
    fake.mark_ready(f"tenant-{user_id}", uuid)
    await reconcile_once(sm)
    inst = (await client.get(f"/api/v1/instances/{uuid}", headers=h)).json()
    assert inst["status"] == "running"

    # ── 5. 接入信息(SSH 指令 + Jupyter URL)─────────────────
    access = (await client.get(f"/api/v1/instances/{uuid}/access", headers=h)).json()
    assert (
        access["ssh_command"].startswith("ssh root@")
        and str(access["ssh_port"]) in access["ssh_command"]
    )
    assert access["jupyter_url"].startswith("https://") and "token=" in access["jupyter_url"]
    # Pod 规格:HAMi 资源 + userns 加固 + JuiceFS 子路径
    pod_spec = fake.pods[(f"tenant-{user_id}", uuid)].spec
    assert pod_spec.gpu_resources["nvidia.com/gpucores"] == "50"
    assert pod_spec.host_users is False
    assert pod_spec.data_disk_subpath == f"disk-{disk['id']}"

    # ── 6. 跑 30 分钟后停机 → 尾账 ─────────────────────────
    from tests.test_billing_flow import backdate_running_event

    expected_secs = await backdate_running_event(sm, uuid, 30)
    assert (await client.post(f"/api/v1/instances/{uuid}/stop", headers=h)).status_code == 200
    await drain(sm)
    await reconcile_once(sm)
    assert (await client.get(f"/api/v1/instances/{uuid}", headers=h)).json()["status"] == "stopped"

    bills = (await client.get("/api/v1/bills/hourly", headers=h)).json()["items"]
    assert len(bills) == 1
    assert expected_secs - 2 <= bills[0]["seconds_used"] <= expected_secs + 15

    # ── 7. 事件时间线 = 计费依据,链路完整 ──────────────────
    events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=h)).json()
    chain = [(e["from_status"], e["to_status"]) for e in events]
    assert chain[0] == (None, "creating")
    assert ("creating", "running") in chain
    assert ("running", "stopping") in chain
    assert ("stopping", "stopped") in chain

    # ── 8. 释放(实例盘清除,数据盘保留)─────────────────────
    assert (await client.delete(f"/api/v1/instances/{uuid}", headers=h)).status_code == 200
    await drain(sm)
    await reconcile_once(sm)
    events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=h)).json()
    assert events[-1]["to_status"] == "released"
    assert events[-1]["event_metadata"]["disk_wipe"] == "blkdiscard"

    disks = (await client.get("/api/v1/disks", headers=h)).json()
    assert disks[0]["status"] == "active" and disks[0]["mounted_instance_id"] is None

    # ── 9. 资金自洽:充值 - 消费 = 余额;流水快照链一致 ─────
    wallet = (await client.get("/api/v1/wallet", headers=h)).json()
    async with sm() as session:
        entries = (
            (
                await session.execute(
                    select(BalanceLedger)
                    .where(BalanceLedger.user_id == user_id)
                    .order_by(BalanceLedger.id)
                )
            )
            .scalars()
            .all()
        )
    running_total = Decimal("0.00")
    for e in entries:
        running_total += e.amount
        assert e.balance_after == running_total
    consumed = -sum((e.amount for e in entries if e.type == "consume"), Decimal("0.00"))
    assert Decimal(wallet["balance"]) == Decimal("200.00") - consumed
    assert consumed == Decimal(bills[0]["amount"])
