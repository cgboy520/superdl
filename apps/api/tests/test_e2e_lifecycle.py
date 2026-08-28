"""E2E 演练:一条脚本跑通全生命周期(FakeOrchestrator + mock 支付)。

注册 → 充值 → 建数据盘 → 买共享档实例(挂盘)→ SSH/Jupyter 接入 → 停机(尾账)→
账单与事件时间线 → 释放(擦盘)→ 数据盘保留 → 全程资金自洽(充值 - 消费 = 余额)。
"""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.k8s import set_orchestrator
from app.core.k8s.fake import FakeOrchestrator
from app.modules.billing.models import BalanceLedger
from app.modules.orchestrator.models import PortAllocation
from app.modules.orchestrator.reconciler import reconcile_once
from tests.helpers import create_test_sku, drain, gen_ed25519_key, seed_node_spec


@pytest.fixture
def fake():
    orch = FakeOrchestrator(auto_ready=False)
    set_orchestrator(orch)
    yield orch
    set_orchestrator(None)


async def test_full_lifecycle_drill(client, sm, fake):
    # ── 1. 注册 ────────────────────────────────────────────────
    phone = "13411112222"
    await client.post(
        "/api/v1/auth/sms-code",
        json={"phone": phone, "purpose": "register"},
    )
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
    await client.post(
        "/api/v1/webhooks/mock",
        json={"order_no": order["order_no"], "amount": "200.00"},
    )
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
    await drain(sm)  # 配额下发完成(quota_synced=true)后才可挂载

    # ── 4. 市场选共享档 → 创建实例(挂盘,Idempotency-Key)──
    sku_id = await create_test_sku(sm)  # 共享标准档 1.68/时 hami 池
    await seed_node_spec(sm)  # 台账:hami 池 32 张 RTX4090 全空闲(市场库存数据源)
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
    # JUPYTER_TOKEN 不落 Pod spec(走 per-instance Secret + secretKeyRef)
    pod = fake.pods[(f"tenant-{user_id}", uuid)]
    assert "JUPYTER_TOKEN" not in pod.spec.env
    assert fake.instance_secrets[(f"tenant-{user_id}", uuid)]["JUPYTER_TOKEN"]
    # 未配 Harbor 机器人(项目 public):不托管拉取凭据、Pod 不引用 imagePullSecrets
    assert pod.spec.image_pull_secret is None and f"tenant-{user_id}" not in fake.pull_secrets
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
    # 一次性 bootstrap 票据:token 不出现在 URL(访问日志/浏览器历史不沉淀长效凭据)
    assert access["jupyter_url"].startswith("https://")
    assert "/superdl-bootstrap?" in access["jupyter_url"]
    assert "token=" not in access["jupyter_url"]
    # Pod 规格:HAMi 资源 + userns 加固 + JuiceFS 子路径
    pod_spec = fake.pods[(f"tenant-{user_id}", uuid)].spec
    assert pod_spec.gpu_resources["nvidia.com/gpucores"] == "50"
    assert pod_spec.host_users is False
    assert pod_spec.data_disk_subpath == f"disk-{disk['uuid']}"

    # ── 6. 跑 30 分钟后停机 → 尾账 ─────────────────────────
    from tests.test_billing_flow import backdate_running_event

    expected_secs = await backdate_running_event(sm, uuid, 30)
    await client.post(f"/api/v1/instances/{uuid}/stop", headers=h)
    await drain(sm)
    await reconcile_once(sm)
    assert (await client.get(f"/api/v1/instances/{uuid}", headers=h)).json()["status"] == "stopped"

    bills = (await client.get("/api/v1/bills/hourly", headers=h)).json()["items"]
    assert len(bills) == 1
    assert expected_secs - 2 <= bills[0]["seconds_used"] <= expected_secs + 15

    # ── 7. 事件时间线 = 计费依据,链路完整 ──────────────────
    events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=h)).json()["items"]
    chain = [(e["from_status"], e["to_status"]) for e in reversed(events)]  # 降序 → 还原时序
    assert chain[0] == (None, "creating")
    assert ("creating", "running") in chain
    assert ("running", "stopping") in chain
    assert ("stopping", "stopped") in chain

    # ── 8. 释放(实例盘清除,数据盘保留)─────────────────────
    await client.delete(f"/api/v1/instances/{uuid}", headers=h)
    await drain(sm)
    await reconcile_once(sm)
    events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=h)).json()["items"]
    assert events[0]["to_status"] == "released"

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


async def test_pull_secret_managed_per_tenant_when_registry_configured(client, sm, fake):
    """配了 Harbor 机器人:建 Pod 前把拉取凭据 Secret 按指纹托管到租户 ns,
    Pod 以 imagePullSecrets 引用;
    改 Secret 后指纹变化(轮换靠它触发覆写)。挂了说明私有项目的镜像会拉不下来,或轮换不生效。"""
    from app.core.platform_config import set_platform_settings
    from app.core.registry import PULL_SECRET_NAME, pull_secret_fingerprint
    from tests.helpers import create_test_sku, create_user_with_key, fund_wallet, seed_node_spec

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
    headers, user_id, key_id = await create_user_with_key(client, "13411113333")
    await fund_wallet(sm, user_id)
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
    """E2E 演练二:部署服务 → 建 Key → 经端点鉴权调用 → 吊销 → 401 → 释放。

    与 test_endpoint_auth.py 的矩阵不重合:那边逐条钉鉴权判据,这里跑一条脚本走完全程,
    守住只有在整链路里才看得见的事 —— 服务型实例不占 SSH 端口池、不建 Jupyter 入口、
    密文 env 不落 Pod spec、全程资金自洽。
    """
    phone = "13411113333"
    await client.post("/api/v1/auth/sms-code", json={"phone": phone, "purpose": "register"})
    reg = await client.post(
        "/api/v1/auth/register", json={"phone": phone, "sms_code": "123456", "accept_terms": True}
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

    # ── 部署服务:不开 SSH、带密文 env、要 API Key ──────────────
    inst = (
        await client.post(
            "/api/v1/instances",
            json={
                "sku_id": sku_id,
                "image_ref": "registry.superdl.local/vllm:v0.6.3",
                "ssh_key_ids": [],
                "workload_type": "service",
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
    uuid = inst["uuid"]
    await drain(sm)
    fake.mark_ready(f"tenant-{user_id}", uuid)
    await reconcile_once(sm)
    assert (await client.get(f"/api/v1/instances/{uuid}", headers=h)).json()["status"] == "running"

    # 不占 SSH 端口池:端口段 30000–32767 是全平台硬上限,白占一个名额就少一台带 SSH 的实例
    async with sm() as session:
        assigned = (
            await session.execute(
                select(PortAllocation).where(PortAllocation.instance_id.is_not(None))
            )
        ).scalars()
        assert list(assigned) == []

    # 密文 env 不落 Pod spec(spec 进 etcd/审计快照,任何 pods:get 身份都读得到)
    pod_spec = fake.pods[(f"tenant-{user_id}", uuid)].spec
    assert "hf_drill_secret" not in str(pod_spec.env)
    assert pod_spec.secret_env["HF_TOKEN"] == "hf_drill_secret"
    assert pod_spec.env["MAX_MODEL_LEN"] == "8192"
    # 服务形态:原地重启 + 走服务端口,不建 Jupyter 入口
    assert pod_spec.restart_policy == "Always"
    assert pod_spec.service_port == 8000 and pod_spec.with_ssh is False

    # ── 端点与 Key ────────────────────────────────────────────
    endpoint = (await client.get(f"/api/v1/instances/{uuid}/service", headers=h)).json()
    slug = endpoint["slug"]
    assert endpoint["url"].endswith(f"{slug}.{get_settings().service_domain_suffix}")
    assert endpoint["require_api_key"] is True
    # 容器配置回显:明文项给值,密文项只给键名
    assert endpoint["env"] == {"MAX_MODEL_LEN": "8192"}
    assert endpoint["env_secret_keys"] == ["HF_TOKEN"]

    created = (
        await client.post(f"/api/v1/instances/{uuid}/api-keys", json={"name": "drill"}, headers=h)
    ).json()
    plain = created["key"]
    assert plain.startswith("sk-")
    # 明文只此一次:列表接口再也拿不到它
    listed = (await client.get(f"/api/v1/instances/{uuid}/api-keys", headers=h)).json()
    assert plain not in str(listed)

    # ── 网关鉴权链路(模拟 Envoy extAuth 回调)────────────────
    auth_url = "/api/internal/v1/endpoint-auth"
    host = {"host": f"{slug}.{get_settings().service_domain_suffix}"}
    ok = await client.post(auth_url, headers={**host, "authorization": f"Bearer {plain}"})
    assert ok.status_code == 200
    # 平台注入头必须回全:没回的头会被客户端伪造值原样透传给用户容器
    assert ok.headers["x-superdl-endpoint"] == slug
    assert ok.headers["x-superdl-key-id"] == str(created["id"])

    # ── 吊销 → 立即 401(网关侧无缓存,吊销即时生效)──────────
    assert (
        await client.delete(f"/api/v1/instances/{uuid}/api-keys/{created['id']}", headers=h)
    ).status_code == 200
    denied = await client.post(auth_url, headers={**host, "authorization": f"Bearer {plain}"})
    assert denied.status_code == 401
    assert denied.json()["code"] == "API_KEY_INVALID"

    # ── 关机 → 释放;全程资金自洽 ─────────────────────────────
    await client.post(f"/api/v1/instances/{uuid}/stop", headers=h)
    await drain(sm)
    fake.finish_delete(f"tenant-{user_id}", uuid)
    await reconcile_once(sm)
    assert (await client.get(f"/api/v1/instances/{uuid}", headers=h)).json()["status"] == "stopped"

    assert (await client.delete(f"/api/v1/instances/{uuid}", headers=h)).status_code in (200, 202)
    await drain(sm)

    # 充值 - 消费 = 余额:服务实例与开发机共用同一套计费,没有第二条账路
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
    # 流水金额带符号,逐条与 balance_after 快照对齐(与开发机那条演练同一套断言):
    # 服务实例走的就是这套账,没有第二条账路
    running = Decimal("0.00")
    for e in entries:
        running += e.amount
        assert e.balance_after == running
    balance = Decimal((await client.get("/api/v1/wallet", headers=h)).json()["balance"])
    assert running == balance


async def test_subscription_drill(client, sm, fake):
    """包周期主链路:充值 → 买包月 → 运行 → 到期 → 停机 → 冻结 → 回收,全程资金自洽。

    与按量演练分开一条:那条跑「跑多久算多少钱」,这条跑「先付一整段、结算完全不参与」。
    挂在中段(到期不停机)= 到期后仍免费在跑;挂在末段(冻结不回收)= 实例盘收不回来;
    挂在资金断言 = 预扣与流水对不上,财务侧无法对账。
    """
    from datetime import timedelta

    from sqlalchemy import update

    from app.core.timeutil import now_utc
    from app.modules.billing.models import BillHourly, Subscription
    from app.modules.billing.patrol import balance_patrol
    from app.modules.billing.subscriptions import subscription_patrol
    from app.modules.orchestrator.models import Instance

    # ── 1. 注册 + 充值 ────────────────────────────────────────
    phone = "13411113333"
    await client.post("/api/v1/auth/sms-code", json={"phone": phone, "purpose": "register"})
    reg = await client.post(
        "/api/v1/auth/register", json={"phone": phone, "sms_code": "123456", "accept_terms": True}
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

    # ── 2. 买一个月(下单即预扣整段周期)────────────────────
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
            "image_ref": "registry.superdl.local/pytorch:2.9.0-cu128",
            "ssh_key_ids": [key.json()["id"]],
            "market": "subscription",
            "period": "month",
            "period_count": 1,
        },
        headers=h,
    )
    assert resp.status_code == 202, resp.text
    uuid = resp.json()["uuid"]
    # ¥3.99/时 × 720 时 × 8 折 —— 与 UI 稿上的数字逐字相同
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

    # ── 3. 到期 → 停机 → 冻结 → 回收 ──────────────────────────
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
    await balance_patrol(sm)  # 回收仍走欠费巡检的既有分支
    await drain(sm)
    await reconcile_once(sm)
    events = (await client.get(f"/api/v1/instances/{uuid}/events", headers=h)).json()["items"]
    assert events[0]["to_status"] == "released"
    chain = [(e["from_status"], e["to_status"]) for e in reversed(events)]
    assert ("running", "stopping") in chain
    assert ("stopped", "frozen") in chain
    assert ("frozen", "releasing") in chain

    # ── 4. 全程零小时账单:预付过的实例不进结算 ───────────────
    async with sm() as session:
        bills = (
            (await session.execute(select(BillHourly).where(BillHourly.instance_id == instance_id)))
            .scalars()
            .all()
        )
    assert bills == []

    # ── 5. 资金自洽:充值 - 预扣 = 余额,流水快照链一致 ────────
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
    assert len(consume) == 1  # 只有下单那一笔,没有任何小时账
    assert consume[0].ref_type == "subscription"
    assert consume[0].amount == Decimal("-2298.24")
    wallet = (await client.get("/api/v1/wallet", headers=h)).json()
    assert Decimal(wallet["balance"]) == Decimal("701.76")
