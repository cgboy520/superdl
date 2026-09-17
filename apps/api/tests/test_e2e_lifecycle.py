"""实例、服务与包周期生命周期的资源和资金契约。"""

from decimal import Decimal

from sqlalchemy import select

from app.core.config import get_settings
from app.modules.billing.models import BalanceLedger
from app.modules.orchestrator.models import PortAllocation
from app.modules.orchestrator.reconciler import reconcile_once
from tests.helpers import (
    IMAGE_PYTORCH,
    as_handle,
    backdate_running_event,
    create_test_sku,
    drain,
    funded_user,
    gen_ed25519_key,
    seed_node_spec,
)


async def test_full_lifecycle_drill(client, sm, fake):
    phone = "13411112222"
    await client.post(
        "/api/v1/auth/verification-code",
        json={"handle": as_handle(phone), "purpose": "register"},
    )
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": as_handle(phone), "email_code": "123456", "accept_terms": True},
    )
    assert reg.status_code == 201
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    user_id = reg.json()["user"]["id"]

    order = (
        await client.post(
            "/api/v1/wallet/recharges", json={"amount": "200.00", "channel": "mock"}, headers=h
        )
    ).json()
    await client.post(
        "/api/v1/webhooks/mock",
        json={"order_no": order["order_no"], "amount": "200.00"},
    )
    assert (await client.get("/api/v1/wallet", headers=h)).json()["balance"] == "200.00"

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
    await drain(sm)

    sku_id = await create_test_sku(sm)
    await seed_node_spec(sm)
    market = (await client.get("/api/v1/skus")).json()
    assert any(s["id"] == sku_id and s["available_count"] > 0 for s in market)

    inst = (
        await client.post(
            "/api/v1/instances",
            json={
                "sku_id": sku_id,
                "image_ref": IMAGE_PYTORCH,
                "ssh_key_ids": [key["id"]],
                "data_disk_id": disk["id"],
            },
            headers={**h, "Idempotency-Key": "drill-1"},
        )
    ).json()
    uuid = inst["uuid"]
    assert inst["status"] == "creating"

    await drain(sm)
    pod = fake.pods[(f"tenant-{user_id}", uuid)]
    assert "JUPYTER_TOKEN" not in pod.spec.env
    assert fake.instance_secrets[(f"tenant-{user_id}", uuid)]["JUPYTER_TOKEN"]
    assert pod.spec.image_pull_secret is None and f"tenant-{user_id}" not in fake.pull_secrets
    fake.mark_ready(f"tenant-{user_id}", uuid)
    await reconcile_once(sm)
    inst = (await client.get(f"/api/v1/instances/{uuid}", headers=h)).json()
    assert inst["status"] == "running"

    access = (await client.get(f"/api/v1/instances/{uuid}/access", headers=h)).json()
    assert (
        access["ssh_command"].startswith("ssh root@")
        and str(access["ssh_port"]) in access["ssh_command"]
    )
    assert access["jupyter_url"].startswith("https://")
    assert "/superdl-bootstrap?" in access["jupyter_url"]
    assert "token=" not in access["jupyter_url"]
    pod_spec = fake.pods[(f"tenant-{user_id}", uuid)].spec
    assert pod_spec.gpu_resources["nvidia.com/gpucores"] == "50"
    assert pod_spec.host_users is False
    assert pod_spec.data_disk_pvc == f"disk-{disk['uuid']}"

    expected_secs = await backdate_running_event(sm, uuid, 30)
    await client.post(f"/api/v1/instances/{uuid}/stop", headers=h)
    await drain(sm)
    await reconcile_once(sm)
    assert (await client.get(f"/api/v1/instances/{uuid}", headers=h)).json()["status"] == "stopped"

    bills = (await client.get("/api/v1/bills/hourly", headers=h)).json()["items"]
    assert len(bills) == 1
    assert expected_secs - 2 <= bills[0]["seconds_used"] <= expected_secs + 15

    events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=h)).json()["items"]
    chain = [(e["from_status"], e["to_status"]) for e in reversed(events)]
    assert chain[0] == (None, "creating")
    assert ("creating", "running") in chain
    assert ("running", "stopping") in chain
    assert ("stopping", "stopped") in chain

    await client.delete(f"/api/v1/instances/{uuid}", headers=h)
    await drain(sm)
    await reconcile_once(sm)
    events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=h)).json()["items"]
    assert events[0]["to_status"] == "released"

    disks = (await client.get("/api/v1/disks", headers=h)).json()
    assert disks[0]["status"] == "active" and disks[0]["mounted_instance_id"] is None

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


async def test_pull_secret_managed_per_tenant_when_registry_configured(client, sm, fake):
    """配了 Harbor 机器人:拉取凭据 Secret 按指纹托管到租户 ns,Pod 以 imagePullSecrets 引用;
    改 Secret 后指纹变化。"""
    from app.core.platform_config import set_platform_settings
    from app.core.registry import PULL_SECRET_NAME, pull_secret_fingerprint

    async with sm() as session:
        await set_platform_settings(
            session,
            {
                "registry_host": "harbor.example.com",
                "registry_robot_name": "robot$superdl+pull",
                "registry_robot_secret": "s3cret-one",
            },
            updated_by=None,
        )
        await session.commit()
    headers, user_id, key_id = await funded_user(client, sm, "13411113333")
    sku_id = await create_test_sku(sm)
    await seed_node_spec(sm)
    resp = await client.post(
        "/api/v1/instances",
        json={
            "sku_id": sku_id,
            "image_ref": "harbor.example.com/superdl/pytorch:2.9.0-cu128",
            "ssh_key_ids": [key_id],
        },
        headers=headers,
    )
    assert resp.status_code == 202, resp.text
    uuid = resp.json()["uuid"]
    await drain(sm)
    ns = f"tenant-{user_id}"
    assert fake.pods[(ns, uuid)].spec.image_pull_secret == PULL_SECRET_NAME
    assert fake.pull_secrets[ns] == pull_secret_fingerprint(
        "harbor.example.com", "robot$superdl+pull", "s3cret-one"
    )
    assert fake.pull_secrets[ns] != pull_secret_fingerprint(
        "harbor.example.com", "robot$superdl+pull", "rotated"
    )


async def test_service_container_drill(client, sm, fake):
    """服务生命周期中端点鉴权、资源隔离与资金账保持一致。"""
    phone = "13411113333"
    await client.post(
        "/api/v1/auth/verification-code", json={"handle": as_handle(phone), "purpose": "register"}
    )
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": as_handle(phone), "email_code": "123456", "accept_terms": True},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    user_id = reg.json()["user"]["id"]

    order = (
        await client.post(
            "/api/v1/wallet/recharges", json={"amount": "200.00", "channel": "mock"}, headers=h
        )
    ).json()
    await client.post(
        "/api/v1/webhooks/mock", json={"order_no": order["order_no"], "amount": "200.00"}
    )

    sku_id = await create_test_sku(sm)
    await seed_node_spec(sm)

    svc = (
        await client.post(
            "/api/v1/services",
            json={
                "sku_id": sku_id,
                "image_ref": "registry.superdl.local/vllm:v0.6.3",
                "ssh_key_ids": [],
                "name": "drill",
                "container_command": ["python"],
                "container_args": ["-m", "vllm.entrypoints.openai.api_server"],
                "env": {"MAX_MODEL_LEN": "8192", "HF_TOKEN": "hf_drill_secret"},
                "env_secret_keys": ["HF_TOKEN"],
                "service_port": 8000,
                "health_path": "/health",
            },
            headers=h,
        )
    ).json()
    slug = svc["slug"]
    uuid = svc["current_instance"]["uuid"]
    assert svc["status"] == "deploying"
    await drain(sm)
    fake.mark_ready(f"tenant-{user_id}", uuid)
    await reconcile_once(sm)
    svc = (await client.get(f"/api/v1/services/{slug}", headers=h)).json()
    assert svc["status"] == "running" and svc["ready"] is True

    async with sm() as session:
        assigned = (
            await session.execute(
                select(PortAllocation).where(PortAllocation.instance_id.is_not(None))
            )
        ).scalars()
        assert list(assigned) == []

    pod_spec = fake.pods[(f"tenant-{user_id}", uuid)].spec
    assert "hf_drill_secret" not in str(pod_spec.env)
    assert pod_spec.secret_env["HF_TOKEN"] == "hf_drill_secret"
    assert pod_spec.env["MAX_MODEL_LEN"] == "8192"
    assert pod_spec.restart_policy == "Always"
    assert pod_spec.service_port == 8000 and pod_spec.with_ssh is False

    listed = (await client.get("/api/v1/instances", headers=h)).json()["items"]
    assert [i["uuid"] for i in listed] == []
    assert (await client.get(f"/api/v1/instances/{uuid}", headers=h)).status_code == 200

    assert svc["url"].endswith(f"{slug}.{get_settings().service_domain_suffix}")
    assert svc["require_api_key"] is True
    assert svc["container"]["env"] == {"MAX_MODEL_LEN": "8192"}
    assert svc["container"]["env_secret_keys"] == ["HF_TOKEN"]

    created = (
        await client.post(f"/api/v1/services/{slug}/api-keys", json={"name": "drill"}, headers=h)
    ).json()
    plain = created["key"]
    assert plain.startswith("sk-")
    listed_keys = (await client.get(f"/api/v1/services/{slug}/api-keys", headers=h)).json()
    assert plain not in str(listed_keys)

    auth_url = "/api/internal/v1/endpoint-auth"
    host = {"host": f"{slug}.{get_settings().service_domain_suffix}"}
    ok = await client.post(auth_url, headers={**host, "authorization": f"Bearer {plain}"})
    assert ok.status_code == 200
    assert ok.headers["x-superdl-endpoint"] == slug
    assert ok.headers["x-superdl-key-id"] == str(created["id"])

    rollout = (
        await client.post(
            f"/api/v1/services/{slug}/revisions",
            json={
                "sku_id": sku_id,
                "image_ref": "registry.superdl.local/vllm:v0.7.0",
                "ssh_key_ids": [],
                "env": {"MAX_MODEL_LEN": "16384"},
                "env_secret_keep": ["HF_TOKEN"],
                "service_port": 8000,
                "health_path": "/health",
            },
            headers=h,
        )
    ).json()
    assert rollout["status"] == "deploying" and rollout["revision"] == 2
    uuid_v2 = rollout["rollout_instance"]["uuid"]
    await drain(sm)
    fake.finish_delete(f"tenant-{user_id}", uuid)
    fake.mark_ready(f"tenant-{user_id}", uuid_v2)
    await reconcile_once(sm)
    await drain(sm)
    await reconcile_once(sm)
    svc = (await client.get(f"/api/v1/services/{slug}", headers=h)).json()
    assert svc["status"] == "running" and svc["current_instance"]["uuid"] == uuid_v2
    assert svc["container"]["image_ref"] == "registry.superdl.local/vllm:v0.7.0"
    assert svc["container"]["env"] == {"MAX_MODEL_LEN": "16384"}
    assert (
        fake.pods[(f"tenant-{user_id}", uuid_v2)].spec.secret_env["HF_TOKEN"] == "hf_drill_secret"
    )
    assert (await client.get(f"/api/v1/instances/{uuid}", headers=h)).json()["status"] == "released"
    assert (
        await client.post(auth_url, headers={**host, "authorization": f"Bearer {plain}"})
    ).status_code == 200
    uuid = uuid_v2

    assert (
        await client.delete(f"/api/v1/services/{slug}/api-keys/{created['id']}", headers=h)
    ).status_code == 200
    denied = await client.post(auth_url, headers={**host, "authorization": f"Bearer {plain}"})
    assert denied.status_code == 401
    assert denied.json()["code"] == "API_KEY_INVALID"

    stopped = await client.post(f"/api/v1/services/{slug}/stop", headers=h)
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["status"] == "stopping"
    await drain(sm)
    fake.finish_delete(f"tenant-{user_id}", uuid)
    await reconcile_once(sm)
    svc = (await client.get(f"/api/v1/services/{slug}", headers=h)).json()
    assert svc["status"] == "stopped" and svc["desired_state"] == "stopped"

    deleted = await client.delete(f"/api/v1/services/{slug}", headers=h)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["status"] == "releasing"
    await drain(sm)
    await reconcile_once(sm)
    svc = (await client.get(f"/api/v1/services/{slug}", headers=h)).json()
    assert svc["status"] == "released" and svc["released_at"] is not None
    assert (await client.get("/api/v1/services", headers=h)).json()["items"] == []

    async with sm() as session:
        entries = list(
            (
                await session.execute(
                    select(BalanceLedger)
                    .where(BalanceLedger.user_id == user_id)
                    .order_by(BalanceLedger.id)
                )
            ).scalars()
        )
    running = Decimal("0.00")
    for e in entries:
        running += e.amount
        assert e.balance_after == running
    balance = Decimal((await client.get("/api/v1/wallet", headers=h)).json()["balance"])
    assert running == balance


async def test_subscription_drill(client, sm, fake):
    """包周期实例到期停机并回收,不产生重复小时账单。"""
    from datetime import timedelta

    from sqlalchemy import update

    from app.core.timeutil import now_utc
    from app.modules.billing.models import BillHourly, Subscription
    from app.modules.billing.patrol import balance_patrol
    from app.modules.billing.subscriptions import subscription_patrol
    from app.modules.orchestrator.models import Instance

    phone = "13411113333"
    await client.post(
        "/api/v1/auth/verification-code", json={"handle": as_handle(phone), "purpose": "register"}
    )
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": as_handle(phone), "email_code": "123456", "accept_terms": True},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    user_id = reg.json()["user"]["id"]
    order = (
        await client.post(
            "/api/v1/wallet/recharges", json={"amount": "3000.00", "channel": "mock"}, headers=h
        )
    ).json()
    await client.post(
        "/api/v1/webhooks/mock", json={"order_no": order["order_no"], "amount": "3000.00"}
    )
    assert Decimal((await client.get("/api/v1/wallet", headers=h)).json()["balance"]) == Decimal(
        "3000.00"
    )

    key = await client.post(
        "/api/v1/ssh-keys", json={"name": "k", "public_key": gen_ed25519_key()}, headers=h
    )
    sku_id = await create_test_sku(
        sm,
        gpu_cores_pct=100,
        vcpu=16,
        mem_gb=64,
        tier="dedicated",
        pool_label="kata",
        gpu_model="RTX4090",
        vram_gb=24,
        price_hourly=Decimal("3.9900"),
        name="RTX4090 · 专用整卡",
    )
    await seed_node_spec(sm, node_name="node-sub", pool_label="kata")
    resp = await client.post(
        "/api/v1/instances",
        json={
            "sku_id": sku_id,
            "gpu_count": 1,
            "image_ref": IMAGE_PYTORCH,
            "ssh_key_ids": [key.json()["id"]],
            "market": "subscription",
            "period": "month",
            "period_count": 1,
        },
        headers=h,
    )
    assert resp.status_code == 202, resp.text
    uuid = resp.json()["uuid"]
    assert Decimal((await client.get("/api/v1/wallet", headers=h)).json()["balance"]) == Decimal(
        "701.76"
    )

    await drain(sm)
    fake.mark_ready(f"tenant-{user_id}", uuid)
    await reconcile_once(sm)
    item = next(
        i
        for i in (await client.get("/api/v1/instances", headers=h)).json()["items"]
        if i["uuid"] == uuid
    )
    assert item["status"] == "running"
    assert item["market"] == "subscription"
    assert item["subscription"]["period"] == "month"

    async with sm() as session:
        await session.execute(
            update(Subscription)
            .where(Subscription.user_id == user_id)
            .values(expires_at=now_utc() - timedelta(minutes=1))
        )
        await session.commit()
    assert (await subscription_patrol(sm))["stopped"] == 1
    await drain(sm)
    await reconcile_once(sm)
    assert (await subscription_patrol(sm))["frozen"] == 1

    async with sm() as session:
        inst = (await session.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
        assert inst.status == "frozen" and inst.frozen_deadline is not None
        await session.execute(
            update(Instance)
            .where(Instance.id == inst.id)
            .values(frozen_deadline=now_utc() - timedelta(minutes=1))
        )
        await session.commit()
        instance_id = inst.id
    await balance_patrol(sm)
    await drain(sm)
    await reconcile_once(sm)
    events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=h)).json()["items"]
    assert events[0]["to_status"] == "released"
    chain = [(e["from_status"], e["to_status"]) for e in reversed(events)]
    assert ("running", "stopping") in chain
    assert ("stopped", "frozen") in chain
    assert ("frozen", "releasing") in chain

    async with sm() as session:
        bills = (
            (await session.execute(select(BillHourly).where(BillHourly.instance_id == instance_id)))
            .scalars()
            .all()
        )
    assert bills == []

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
    consume = [e for e in entries if e.type == "consume"]
    assert len(consume) == 1
    assert consume[0].ref_type == "subscription"
    assert consume[0].amount == Decimal("-2298.24")
    wallet = (await client.get("/api/v1/wallet", headers=h)).json()
    assert Decimal(wallet["balance"]) == Decimal("701.76")
