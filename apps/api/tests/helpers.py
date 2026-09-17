"""共享测试数据构造、API 请求与 outbox/reconciler 驱动助手。"""

# pyright: reportPrivateUsage=false

import asyncio
import base64
import json
import os
import secrets
import struct
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx
import pyotp
from httpx import AsyncClient, Response
from kubernetes.config import kube_config
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import outbox
from app.core.crypto import hash_verification_code
from app.core.handles import parse_handle
from app.core.k8s.fake import FakeOrchestrator
from app.core.platform_config import set_platform_settings
from app.core.timeutil import now_utc
from app.modules.account.models import VerificationCode
from app.modules.adminapi.auth_service import create_admin
from app.modules.billing import service as billing_service, wallet
from app.modules.catalog.models import PlatformImage, Sku
from app.modules.orchestrator.models import DataDisk, Instance, InstanceEvent
from app.modules.orchestrator.reconciler import reconcile_once
from app.modules.orchestrator.service import _encode_token

IMAGE_PYTORCH = "registry.superdl.local/pytorch:2.9.0-cu128"


def use_kubeconfig(path: str) -> None:
    """设置 KUBECONFIG 环境变量与 Kubernetes 客户端默认路径。"""
    os.environ["KUBECONFIG"] = path
    kube_config.KUBE_CONFIG_DEFAULT_LOCATION = path


PVC_BOUND_TIMEOUT = 30.0


async def wait_pvc_bound(core: Any, namespace: str, name: str) -> None:
    """等 PVC 进入 Bound(apiserver 只允许已绑定的 PVC 扩容)。"""
    deadline = asyncio.get_running_loop().time() + PVC_BOUND_TIMEOUT
    while True:
        pvc: Any = await asyncio.to_thread(
            core.read_namespaced_persistent_volume_claim, name, namespace
        )
        if pvc.status is not None and pvc.status.phase == "Bound":
            return
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError(f"pvc {name} 未在 {PVC_BOUND_TIMEOUT}s 内绑定")
        await asyncio.sleep(0.5)


async def drain(
    sm: async_sessionmaker[AsyncSession],
    *,
    limit: int = 100,
    task_types: frozenset[str] | None = None,
) -> int:
    """处理最多 limit 个可执行 outbox 任务,返回处理次数。"""
    n = 0
    while n < limit and await outbox.process_one(sm, task_types=task_types):
        n += 1
    return n


async def drain_strict(
    sm: async_sessionmaker[AsyncSession], *, limit: int = 100
) -> tuple[int, int]:
    """处理最多 limit 个任务;有失败则抛 RuntimeError,否则返回 (完成数, 0)。"""
    done = failed = 0
    while done + failed < limit:
        result = await outbox._process_one(sm)
        if result is None:
            break
        if result == "done":
            done += 1
        else:
            failed += 1
    if failed:
        raise RuntimeError(f"outbox drain 未全成功: done={done}, failed={failed}")
    return done, failed


def gen_ed25519_key(comment: str = "t@test") -> str:
    """生成随机字节填充的 SSH ed25519 公钥文本。"""
    blob = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + secrets.token_bytes(32)
    return f"ssh-ed25519 {base64.b64encode(blob).decode()} {comment}"


async def fund_wallet(
    sm: async_sessionmaker[AsyncSession], user_id: int, amount: str = "100.00"
) -> None:
    async with sm() as session:
        await billing_service.credit(
            session, user_id, Decimal(amount), type_="recharge", remark="test-fund"
        )
        await session.commit()


async def create_user_with_key(
    client: AsyncClient, phone: str = "13900000001"
) -> tuple[dict[str, str], int, int]:
    """注册用户 + 添加 SSH 公钥。返回 (headers, user_id, ssh_key_id)。"""
    data = await register(client, phone)
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    resp = await client.post(
        "/api/v1/ssh-keys",
        json={"name": "test", "public_key": gen_ed25519_key()},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return headers, data["user"]["id"], resp.json()["id"]


async def create_test_sku(sm: async_sessionmaker[AsyncSession], **overrides) -> int:
    """按业务键复用或创建 SKU;已有 SKU 的价格或最大卡数不匹配时抛 AssertionError。"""
    async with sm() as session:
        wanted = make_sku(**overrides)
        existing = (
            await session.execute(
                select(Sku).where(
                    Sku.gpu_model == wanted.gpu_model,
                    Sku.tier == wanted.tier,
                    Sku.pool_label == wanted.pool_label,
                    Sku.mig_profile.is_(None)
                    if wanted.mig_profile is None
                    else Sku.mig_profile == wanted.mig_profile,
                    Sku.gpu_cores_pct == wanted.gpu_cores_pct,
                    Sku.vcpu == wanted.vcpu,
                    Sku.mem_gb == wanted.mem_gb,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            same = (
                existing.price_hourly == wanted.price_hourly
                and existing.max_gpus_per_instance == wanted.max_gpus_per_instance
            )
            if not same:
                raise AssertionError(
                    "同业务键 SKU 已存在但字段不同:测试应换型号/份额或复用已有 SKU"
                )
            return existing.id
        session.add(wanted)
        await session.commit()
        return wanted.id


async def seed_node_spec(
    sm: async_sessionmaker[AsyncSession],
    *,
    node_name: str = "node-1",
    pool_label: str = "hami",
    gpu_model: str | None = "RTX4090",
    gpu_count: int = 32,
    gpu_used: int = 0,
    status: str = "Ready",
    vcpu: int = 64,
    mem_gb: int = 256,
    unlabeled: bool = False,
    gpu_model_raw: str | None = None,
    label_synced: bool = False,
    vram_gb: int = 0,
    disk_gb: int = 0,
) -> None:
    """写入一条节点台账,最后发现时间为当前时间。"""
    from app.core.timeutil import now_utc
    from app.modules.nodes.models import NodeSpec

    async with sm() as session:
        session.add(
            NodeSpec(
                node_name=node_name,
                pool_label=pool_label,
                unlabeled=unlabeled,
                gpu_model_raw=gpu_model_raw,
                gpu_model=gpu_model,
                label_synced=label_synced,
                gpu_count=gpu_count,
                gpu_used=gpu_used,
                vram_gb=vram_gb,
                vcpu=vcpu,
                mem_gb=mem_gb,
                disk_gb=disk_gb,
                status=status,
                last_seen=now_utc(),
            )
        )
        await session.commit()


def as_handle(value: str) -> str:
    """Tests historically identify users by bare PRC mobile numbers; map those to a deterministic
    email so the same literals keep working with email-primary accounts."""
    if "@" in value or value.startswith("+"):
        return value
    digits = "".join(ch for ch in value if ch.isdigit())
    return f"u{digits}@test.local"


async def send_code(
    client: AsyncClient, handle: str = "13800000001", purpose: str = "register"
) -> None:
    resp = await client.post(
        "/api/v1/auth/verification-code",
        json={"handle": as_handle(handle), "purpose": purpose},
    )
    assert resp.status_code == 204, resp.text


async def age_sms_codes(sm: async_sessionmaker[AsyncSession]) -> None:
    """Backdate every verification code by two minutes."""
    async with sm() as session:
        await session.execute(
            update(VerificationCode).values(created_at=now_utc() - timedelta(minutes=2))
        )
        await session.commit()


async def register(
    client: AsyncClient, handle: str = "13800000001", password: str | None = None
) -> dict:
    """Register by email (bare digits are mapped through as_handle) with the mock code."""
    email = as_handle(handle)
    await send_code(client, email, "register")
    body: dict = {"email": email, "email_code": "123456", "accept_terms": True}
    if password:
        body["password"] = password
    resp = await client.post("/api/v1/auth/register", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


REFRESH_COOKIE = "superdl_refresh"


def current_refresh_token(client: AsyncClient) -> str:
    token = next(
        (c.value for c in client.cookies.jar if c.name == REFRESH_COOKIE),
        None,
    )
    assert token is not None, "jar 里没有 refresh cookie(注册/登录后会自动种下)"
    return token


async def refresh_via_cookie(client: AsyncClient, token: str | None = None) -> Response:
    """经 cookie 刷新,附带 X-Requested-With;给定 token 时先写入 cookie jar。"""
    if token is not None:
        client.cookies.set(REFRESH_COOKIE, token, path="/")
    return await client.post("/api/v1/auth/refresh", headers={"X-Requested-With": "fetch"})


async def issue_code(sm, handle: str, purpose: str, code: str = "123456") -> None:
    """Insert a verification code row (expires in five minutes) for an email or E.164 handle."""
    parsed = parse_handle(as_handle(handle))
    channel = "email" if parsed.kind == "email" else "sms"
    async with sm() as session:
        session.add(
            VerificationCode(
                channel=channel,
                target=parsed.value,
                code_hash=hash_verification_code(channel, parsed.value, purpose, code),
                purpose=purpose,
                expires_at=now_utc() + timedelta(minutes=5),
            )
        )
        await session.commit()


def make_sku(**overrides) -> Sku:
    defaults = {
        "name": "RTX 4090 · 共享标准",
        "gpu_model": "RTX4090",
        "tier": "shared",
        "gpu_cores_pct": 50,
        "vram_gb": 8,
        "oversell_cores": Decimal("1.50"),
        "pool_label": "hami",
        "vcpu": 8,
        "mem_gb": 32,
        "disk_gb": 100,
        "price_hourly": Decimal("1.6800"),
        "max_gpus_per_instance": 1,
        "cuda_max": "12.8",
        "status": "on",
    }
    defaults.update(overrides)
    return Sku(**defaults)


async def seed_bill_hourly(
    sm: async_sessionmaker[AsyncSession],
    user_id: int,
    *,
    rows: list[tuple[int, datetime, str]],
    unit_price: str = "1.0000",
    seconds: int = 3600,
) -> None:
    """批量写入小时账单;rows 元素为 (instance_id, hour_start, amount)。"""
    from app.modules.billing.models import BillHourly

    async with sm() as session:
        for instance_id, hour_start, amount in rows:
            session.add(
                BillHourly(
                    user_id=user_id,
                    instance_id=instance_id,
                    hour_start=hour_start,
                    seconds_used=seconds,
                    unit_price=Decimal(unit_price),
                    gpu_count=1,
                    amount=Decimal(amount),
                )
            )
        await session.commit()


async def seed_skus(sm: async_sessionmaker[AsyncSession]) -> None:
    async with sm() as session:
        session.add_all(
            [
                make_sku(),
                make_sku(
                    name="RTX 4090 · 独享",
                    tier="dedicated",
                    gpu_cores_pct=100,
                    vram_gb=24,
                    pool_label="kata",
                    price_hourly=Decimal("3.9900"),
                    max_gpus_per_instance=8,
                ),
                make_sku(
                    name="A100 · 独享(下架)",
                    gpu_model="A100",
                    tier="dedicated",
                    vram_gb=80,
                    pool_label="kata",
                    price_hourly=Decimal("9.9900"),
                    status="off",
                ),
            ]
        )
        session.add(
            PlatformImage(
                framework="PyTorch",
                framework_version="2.9.0",
                python_version="3.12",
                cuda_version="12.8",
                image_ref=IMAGE_PYTORCH,
            )
        )
        await session.commit()


async def admin_login(client: AsyncClient, username: str, password: str = "pass1234") -> Response:
    """发起管理端密码登录,返回未经解析的响应。"""
    return await client.post(
        "/api/admin/v1/auth/login", json={"username": username, "password": password}
    )


async def complete_mfa_setup_with_secret(client: AsyncClient, ticket: str) -> tuple[str, str]:
    """mfa_setup 票 → begin → confirm(当前 TOTP)→ (access token, TOTP secret)。"""
    begin = await client.post("/api/admin/v1/auth/mfa/setup/begin", json={"ticket": ticket})
    assert begin.status_code == 200, begin.text
    secret = begin.json()["secret"]
    confirm = await client.post(
        "/api/admin/v1/auth/mfa/setup/confirm",
        json={"ticket": ticket, "code": pyotp.TOTP(secret).now()},
    )
    assert confirm.status_code == 200, confirm.text
    return confirm.json()["access_token"], secret


async def complete_mfa_setup(client: AsyncClient, ticket: str) -> str:
    """mfa_setup 票 → begin → confirm(当前 TOTP)→ access token。"""
    token, _secret = await complete_mfa_setup_with_secret(client, ticket)
    return token


async def admin_headers(
    sm: async_sessionmaker[AsyncSession],
    client: AsyncClient,
    role: str = "admin",
    *,
    username: str | None = None,
) -> dict[str, str]:
    """建管理员(默认用户名 {role}-user)并走完 TOTP 绑定流拿正式 token。"""
    name = username or f"{role}-user"
    async with sm() as session:
        await create_admin(session, name, "pass1234", role)
    resp = await admin_login(client, name)
    assert resp.status_code == 200, resp.text
    token = await complete_mfa_setup(client, resp.json()["ticket"])
    return {"Authorization": f"Bearer {token}"}


def _with_idem(headers: dict[str, str], idem: str | None) -> dict[str, str]:
    h = dict(headers)
    if idem:
        h["Idempotency-Key"] = idem
    return h


async def create_instance_api(
    client: AsyncClient,
    headers: dict[str, str],
    sku_id: int,
    key_id: int,
    *,
    gpu_count: int = 1,
    idem: str | None = None,
) -> dict:
    resp = await client.post(
        "/api/v1/instances",
        json={
            "sku_id": sku_id,
            "gpu_count": gpu_count,
            "image_ref": IMAGE_PYTORCH,
            "ssh_key_ids": [key_id],
        },
        headers=_with_idem(headers, idem),
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


async def get_instance(client: AsyncClient, headers: dict, uuid: str) -> dict:
    resp = await client.get(f"/api/v1/instances/{uuid}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def provision_running(client, sm, fake, phone="13900000010") -> tuple[dict, str, int]:
    """建好一台 running 实例。返回 (headers, uuid, user_id)。"""
    headers, user_id, key_id, sku_id = await new_user(client, sm, phone)
    data = await create_instance_api(client, headers, sku_id, key_id)
    await drain(sm)
    fake.mark_ready(f"tenant-{user_id}", data["uuid"])
    await reconcile_once(sm)
    return headers, data["uuid"], user_id


async def user_headers(client: AsyncClient, phone: str = "13700000001") -> dict[str, str]:
    data = await register(client, phone)
    return {"Authorization": f"Bearer {data['access_token']}"}


async def user_headers_with_id(client: AsyncClient, phone: str) -> tuple[dict[str, str], int]:
    data = await register(client, phone)
    return {"Authorization": f"Bearer {data['access_token']}"}, data["user"]["id"]


async def create_order(client: AsyncClient, headers: dict, amount: str = "50.00") -> dict:
    resp = await client.post(
        "/api/v1/wallet/recharges", json={"amount": amount, "channel": "mock"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def pay_mock(client: AsyncClient, order_no: str, amount: str, txn_id: str | None = None):
    return await client.post(
        "/api/v1/webhooks/mock",
        json={"order_no": order_no, "amount": amount, "txn_id": txn_id or f"tx-{order_no}"},
    )


async def paid_order(client: AsyncClient, headers: dict, amount: str = "50.00") -> dict:
    """创建充值订单并完成 mock 支付;返回创建订单时的响应数据。"""
    order = await create_order(client, headers, amount)
    resp = await pay_mock(client, order["order_no"], amount)
    assert resp.status_code == 200, resp.text
    return order


async def apply_refund(
    client: AsyncClient,
    headers: dict,
    order_no: str,
    amount: str = "50.00",
    idem: str | None = None,
):
    return await client.post(
        "/api/v1/wallet/refunds",
        json={"order_no": order_no, "amount": amount, "reason": "用不完,申请退款"},
        headers=_with_idem(headers, idem),
    )


async def finance_pair(sm, client: AsyncClient) -> tuple[dict, dict]:
    """返回两名不同 finance 管理员的认证 headers。"""
    reviewer = await admin_headers(sm, client, role="finance")
    payer = await admin_headers(sm, client, role="finance", username="finance-payer")
    return reviewer, payer


async def create_disk(client, headers, name="data-1", size_gb=100) -> dict:
    resp = await client.post(
        "/api/v1/disks", json={"name": name, "size_gb": size_gb}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def backdate_running_event(
    sm: async_sessionmaker[AsyncSession], uuid: str, minutes: int
) -> int:
    """把进入 running 的事件回拨(钳制在当前自然小时内),返回预期已运行秒数(近似)。"""
    from app.core.timeutil import hour_floor

    now = now_utc()
    start = max(hour_floor(now), now - timedelta(minutes=minutes))
    async with sm() as session:
        inst = (await session.execute(select(Instance).where(Instance.uuid == uuid))).scalar_one()
        await session.execute(
            update(InstanceEvent)
            .where(InstanceEvent.instance_id == inst.id, InstanceEvent.to_status == "running")
            .values(created_at=start)
        )
        await session.commit()
    return int((now - start).total_seconds())


H = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
H_END = datetime(2026, 8, 19, 11, 0, tzinfo=UTC)


async def seed_instance(
    sm: async_sessionmaker[AsyncSession],
    user_id: int = 1,
    price: str = "1.6800",
    gpu_count: int = 1,
    events: list[tuple] | None = None,
    status: str = "stopped",
    market: str = "on_demand",
    *,
    wallet_credit: bool = True,
    name: str = "t",
    spec: dict | None = None,
    sku_id: int = 1,
    node_name: str | None = None,
) -> tuple[int, str]:
    """直接落库实例 + 事件,返回 (instance_id, uuid)。
    events 元素:(ts, from, to) 或 (ts, from, to, metadata);wallet_credit=True 预存 100.00。
    """
    async with sm() as session:
        inst = Instance(
            uuid=f"u{user_id}i{uuid4().hex[:12]}",
            user_id=user_id,
            name=name,
            sku_id=sku_id,
            spec=spec
            if spec is not None
            else {
                "tier": "shared",
                "vram_gb": 8,
                "vcpu": 8,
                "mem_gb": 32,
                "disk_gb": 100,
                "pool_label": "hami",
                "gpu_cores_pct": 50,
            },
            price_hourly=Decimal(price),
            gpu_count=gpu_count,
            market=market,
            image_ref="img",
            status=status,
            k8s_namespace=f"tenant-{user_id}",
            jupyter_token="tok",
            authorized_keys=[],
            node_name=node_name,
        )
        session.add(inst)
        await session.flush()
        for e in events or []:
            ts, from_s, to_s = e[0], e[1], e[2]
            session.add(
                InstanceEvent(
                    instance_id=inst.id,
                    from_status=from_s,
                    to_status=to_s,
                    reason="seed",
                    actor="system",
                    event_metadata=e[3] if len(e) > 3 else None,
                    created_at=ts,
                )
            )
        if wallet_credit:
            await wallet.credit(
                session, user_id, Decimal("100.00"), type_="recharge", remark="seed"
            )
        await session.commit()
        return inst.id, inst.uuid


async def seed_disk(
    sm: async_sessionmaker[AsyncSession],
    user_id: int,
    *,
    status: str = "active",
    size_gb: int = 100,
    price: str = "0.3500",
    created_at: datetime | None = None,
) -> tuple[int, str]:
    """直接落库一块数据盘,返回 (disk_id, uuid)。"""
    async with sm() as session:
        disk = DataDisk(
            uuid=f"d{user_id}{now_utc().timestamp()}".replace(".", ""),
            user_id=user_id,
            name="t",
            size_gb=size_gb,
            price_gb_month=Decimal(price),
            status=status,
            created_at=created_at or now_utc(),
        )
        session.add(disk)
        await session.commit()
        return disk.id, disk.uuid


def prom_mock(values: list[tuple[float, float]] | None = None, *, fail: bool = False):
    """返回固定 Prometheus 矩阵响应的客户端;fail=True 时返回 HTTP 500。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            return httpx.Response(500, text="down")
        body = {
            "status": "success",
            "data": {
                "result": (
                    [{"metric": {}, "values": [[ts, str(v)] for ts, v in values]}] if values else []
                )
            },
        }
        return httpx.Response(200, text=json.dumps(body))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://prom")


CREATE_BODY = {"pool": "hami", "hostname": "gpu-node-7", "note": "机柜 A3", "ttl_hours": 24}


async def set_platform_setting(sm: async_sessionmaker[AsyncSession], key: str, value: str) -> None:
    async with sm() as session:
        await set_platform_settings(session, {key: value}, updated_by=None)
        await session.commit()


def service_body(sku_id: int, **over) -> dict:
    """构造在线服务请求体,默认不附 SSH 公钥、服务端口为 8000;over 可覆盖字段。"""
    body = {
        "sku_id": sku_id,
        "gpu_count": 1,
        "image_ref": "registry.superdl.local/vllm:0.11.0",
        "ssh_key_ids": [],
        "service_port": 8000,
    }
    body.update(over)
    return body


async def funded_user(
    client: AsyncClient, sm, phone: str, amount: str = "100.00"
) -> tuple[dict[str, str], int, int]:
    """注册 + 加 SSH 公钥 + 充值。返回 (headers, user_id, ssh_key_id)。"""
    headers, user_id, key_id = await create_user_with_key(client, phone)
    await fund_wallet(sm, user_id, amount)
    return headers, user_id, key_id


async def new_user(client: AsyncClient, sm, phone: str) -> tuple[dict[str, str], int, int, int]:
    """funded_user + 建默认 SKU。返回 (headers, user_id, ssh_key_id, sku_id)。"""
    headers, user_id, key_id = await funded_user(client, sm, phone)
    return headers, user_id, key_id, await create_test_sku(sm)


async def provision_service(
    client: AsyncClient,
    sm: async_sessionmaker[AsyncSession],
    fake: FakeOrchestrator,
    *,
    phone: str = "13900000301",
    **over,
) -> tuple[dict[str, str], dict, int]:
    """部署一个 running 的在线服务。返回 (headers, 服务出参, user_id);
    版本实例的 uuid 在 svc["current_instance"]["uuid"]。"""
    headers, user_id, key_id, sku_id = await new_user(client, sm, phone)
    over.setdefault("ssh_key_ids", [key_id] if over.get("with_ssh") else [])
    resp = await client.post("/api/v1/services", json=service_body(sku_id, **over), headers=headers)
    assert resp.status_code == 202, resp.text
    svc = resp.json()
    await drain_strict(sm)
    fake.mark_ready(f"tenant-{user_id}", svc["current_instance"]["uuid"])
    await reconcile_once(sm)
    fresh = await client.get(f"/api/v1/services/{svc['slug']}", headers=headers)
    assert fresh.status_code == 200, fresh.text
    return headers, fresh.json(), user_id


def gpu_spec(tier: str, pool: str, **extra):
    base = {
        "tier": tier,
        "pool_label": pool,
        "vram_gb": 24,
        "gpu_cores_pct": 50,
        "mig_profile": "1g.10gb" if pool == "mig" else None,
        "vcpu": 8,
        "mem_gb": 32,
        "disk_gb": 100,
        "gpu_model": "NVIDIA GeForce RTX 4090",
    }
    base.update(extra)
    return base


def make_instance(**overrides) -> Instance:
    """构造不落库的 Instance;默认签发绑定 uuid 的 jupyter_token,字段可由 overrides 覆盖。"""
    uuid = overrides.get("uuid") or f"inst-{uuid4().hex[:8]}"
    defaults: dict = {
        "user_id": 1,
        "uuid": uuid,
        "name": "t",
        "sku_id": 1,
        "spec": gpu_spec("shared", "hami"),
        "price_hourly": Decimal("1.0000"),
        "gpu_count": 1,
        "image_ref": "img:latest",
        "ssh_port": 30022,
        "jupyter_token": _encode_token("tok", instance_uuid=uuid),
        "authorized_keys": [],
        "data_disk_id": None,
        "k8s_namespace": "tenant-1",
        "status": "creating",
    }
    defaults.update(overrides)
    return Instance(**defaults)


async def buy_subscription(
    client,
    headers,
    sku_id: int,
    key_id: int,
    *,
    period: str = "month",
    period_count: int = 1,
    idem: str | None = None,
) -> tuple[int, dict]:
    resp = await client.post(
        "/api/v1/instances",
        json={
            "sku_id": sku_id,
            "gpu_count": 1,
            "image_ref": IMAGE_PYTORCH,
            "ssh_key_ids": [key_id],
            "market": "subscription",
            "period": period,
            "period_count": period_count,
        },
        headers=_with_idem(headers, idem),
    )
    return resp.status_code, resp.json()


async def provision_subscription(client, sm, fake, phone: str, *, period: str = "month", **kw):
    """建好一台 running 的包周期实例。返回 (headers, uuid, user_id, sku_id, key_id)。"""
    headers, user_id, key_id = await create_user_with_key(client, phone)
    await fund_wallet(sm, user_id, kw.pop("fund", "5000.00"))
    sku_id = await create_test_sku(sm, **kw.pop("sku", {}))
    await seed_node_spec(sm, node_name=f"node-{phone[-4:]}")
    code, data = await buy_subscription(client, headers, sku_id, key_id, period=period)
    assert code == 202, data
    await drain(sm)
    fake.mark_ready(f"tenant-{user_id}", data["uuid"])
    await reconcile_once(sm)
    return headers, data["uuid"], user_id, sku_id, key_id
