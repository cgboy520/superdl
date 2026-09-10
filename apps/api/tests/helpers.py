"""测试共享助手:造用户/密钥/SKU/管理员/钱包,注册与登录,驱动 outbox 与 reconciler。

跨用例复用的助手一律落在这里。测试模块之间不互相 import 助手 —— 那样为了一个
两行的登录函数会连带 import 对方整个测试类树,还会绕出 helpers ↔ 测试模块的循环。
"""

import base64
import json
import os
import secrets
import struct
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pyotp
from httpx import AsyncClient, Response
from kubernetes.config import kube_config
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import outbox
from app.core.crypto import hash_sms_code
from app.core.k8s.fake import FakeOrchestrator
from app.core.platform_config import set_platform_settings
from app.core.timeutil import now_utc
from app.modules.account.models import SmsCode
from app.modules.adminapi.service import create_admin
from app.modules.billing import service as billing_service
from app.modules.billing import wallet
from app.modules.catalog.models import PlatformImage, Sku
from app.modules.orchestrator.models import DataDisk, Instance, InstanceEvent
from app.modules.orchestrator.reconciler import reconcile_once
from app.modules.orchestrator.service import _encode_token

# 平台预置镜像(seed_skus 的 PlatformImage 同源):创建请求体里的统一 image_ref
IMAGE_PYTORCH = "registry.superdl.local/pytorch:2.9.0-cu128"


def use_kubeconfig(path: str) -> None:
    """把 kubeconfig 路径喂给官方客户端(随后构造的 RealOrchestrator 即用此身份)。

    kubernetes 客户端在 import 时就把 KUBECONFIG 固化进了模块常量,之后再改环境变量
    对 load_kube_config() 无效 —— 只改环境变量的话,受限身份会静默退回默认(admin)
    kubeconfig,RBAC 对齐闸变成空跑。两者同时改才真正切身份。
    """
    os.environ["KUBECONFIG"] = path
    kube_config.KUBE_CONFIG_DEFAULT_LOCATION = path


async def drain(
    sm: async_sessionmaker[AsyncSession],
    *,
    limit: int = 100,
    task_types: frozenset[str] | None = None,
) -> int:
    """连续处理 outbox 直到队列空(或到 limit),返回处理个数。

    只是测试驱动手段,生产 worker 关停不做冲刷。失败任务静默滑进重试;
    要断言「全部成功」用 drain_strict。
    """
    n = 0
    while n < limit and await outbox.process_one(sm, task_types=task_types):
        n += 1
    return n


async def drain_strict(
    sm: async_sessionmaker[AsyncSession], *, limit: int = 100
) -> tuple[int, int]:
    """drain 的严格变体:返回 (done_count, failed_count);任一任务未成功
    (dead,或失败退避回 pending 等下轮)即抛 RuntimeError(消息含 done/failed 计数)。"""
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
    """构造合法 ed25519 公钥(随机 32 字节),每次调用指纹唯一。"""
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
    """按业务唯一键 get-or-create,键与 catalog/models.py 的 uq_skus_business_key 一致。

    同一用例内多次 provisioning 复用同一条而不是撞约束;同键但其余字段不同的请求直接报错。
    键漏字段会让本该各建一条的两个 SKU 误判成同一条,报出误导性的断言。
    """
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
    """写一条节点台账(node_specs):市场近似库存与创建软准入的唯一数据源。

    巡检(60s)在真实环境写这张表;测试里显式播种等价于「巡检已跑过一轮」。
    """
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
                # vCPU/内存是 CPU 档库存口径的数据源(GPU 档不看这两列)
                vcpu=vcpu,
                mem_gb=mem_gb,
                disk_gb=disk_gb,
                status=status,
                last_seen=now_utc(),
            )
        )
        await session.commit()


async def send_code(
    client: AsyncClient, phone: str = "13800000001", purpose: str = "register"
) -> None:
    resp = await client.post(
        "/api/v1/auth/sms-code",
        json={"phone": phone, "purpose": purpose},
    )
    assert resp.status_code == 204, resp.text


async def age_sms_codes(sm: async_sessionmaker[AsyncSession]) -> None:
    """把既有验证码的 created_at 回拨,越过 60s 限频窗口(不影响有效期)。"""
    async with sm() as session:
        await session.execute(update(SmsCode).values(created_at=now_utc() - timedelta(minutes=2)))
        await session.commit()


async def register(
    client: AsyncClient, phone: str = "13800000001", password: str | None = None
) -> dict:
    await send_code(client, phone, "register")
    body: dict = {"phone": phone, "sms_code": "123456", "accept_terms": True}
    if password:
        body["password"] = password
    resp = await client.post("/api/v1/auth/register", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


# refresh token 的 cookie 名(非 prod;prod 为 __Host- 前缀,见 account/router.py)。
# 响应体不含 refresh_token,测试从 cookie jar 取
REFRESH_COOKIE = "superdl_refresh"


def current_refresh_token(client: AsyncClient) -> str:
    """jar 里的当前 refresh token(注册/登录/刷新成功后由 Set-Cookie 种下)。"""
    token = next(
        (c.value for c in client.cookies.jar if c.name == REFRESH_COOKIE),
        None,
    )
    assert token is not None, "jar 里没有 refresh cookie(注册/登录后会自动种下)"
    return token


async def refresh_via_cookie(client: AsyncClient, token: str | None = None) -> Response:
    """cookie 通道刷新(强制 X-Requested-With 双提交头):给定 token 先覆写 jar
    (重放/轮换测试)。并发多路刷新请各起一个 client(独立 jar,见 test_token_rotation):
    共享 jar 会让「响应 Set-Cookie 先落 jar」与「请求取 cookie」形成竞态。"""
    if token is not None:
        client.cookies.set(REFRESH_COOKIE, token, path="/")
    return await client.post("/api/v1/auth/refresh", headers={"X-Requested-With": "fetch"})


async def issue_code(sm, phone: str, purpose: str, code: str = "123456") -> None:
    """直接落一条验证码(绕开 60s 发送间隔;注册助手刚发过码时不能再发)。"""
    async with sm() as session:
        session.add(
            SmsCode(
                phone=phone,
                code_hash=hash_sms_code(phone, purpose, code),
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
    rows: list[tuple[int, datetime, str]],  # (instance_id, hour_start, amount)
    unit_price: str = "1.0000",
    seconds: int = 3600,
) -> None:
    """批量播种小时账单(bill_hourly):日聚合/CSV 导出类用例共用。"""
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
    """管理端密码登录(第一步):返回原始响应(按 status 分支:mfa_setup/mfa_required/ok)。"""
    return await client.post(
        "/api/admin/v1/auth/login", json={"username": username, "password": password}
    )


async def complete_mfa_setup_with_secret(client: AsyncClient, ticket: str) -> tuple[str, str]:
    """mfa_setup 票 → begin → confirm(当前 TOTP)→ (access token, TOTP secret)。

    secret 供「同账号再次登录要走二要素验证」的用例登记(测试进程内保存)。
    """
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
    """mfa_setup 票 → begin → confirm(当前 TOTP)→ access token。供管理端测试复用。"""
    token, _secret = await complete_mfa_setup_with_secret(client, ticket)
    return token


async def admin_headers(
    sm: async_sessionmaker[AsyncSession],
    client: AsyncClient,
    role: str = "admin",
    *,
    username: str | None = None,
) -> dict[str, str]:
    """建管理员(默认用户名 {role}-user)并登录到正式 token:全角色强制 TOTP,
    登录只回绑定票,走完整绑定流。"""
    name = username or f"{role}-user"
    async with sm() as session:
        await create_admin(session, name, "pass1234", role)
    resp = await admin_login(client, name)
    assert resp.status_code == 200, resp.text
    token = await complete_mfa_setup(client, resp.json()["ticket"])
    return {"Authorization": f"Bearer {token}"}


def _with_idem(headers: dict[str, str], idem: str | None) -> dict[str, str]:
    """复制 headers 并按需并入 Idempotency-Key(不改调用方原 dict)。"""
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
    headers, user_id, key_id = await create_user_with_key(client, phone)
    await fund_wallet(sm, user_id)
    sku_id = await create_test_sku(sm)
    data = await create_instance_api(client, headers, sku_id, key_id)
    await drain(sm)
    fake.mark_ready(f"tenant-{user_id}", data["uuid"])
    await reconcile_once(sm)
    return headers, data["uuid"], user_id


async def user_headers(client: AsyncClient, phone: str = "13700000001") -> dict[str, str]:
    data = await register(client, phone)
    return {"Authorization": f"Bearer {data['access_token']}"}


async def user_headers_with_id(client: AsyncClient, phone: str) -> tuple[dict[str, str], int]:
    """user_headers 的同路变体:连带返回 user_id(注册响应本就带,省一次 GET /me)。"""
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
    """mock 渠道充值并支付,返回已入账订单。"""
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
    """两名 finance 管理员(审批人与打款人必须不同)。"""
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
    """把进入 running 的事件回拨(钳制在当前自然小时内,尾账只覆盖当前小时)。

    返回预期已运行秒数(近似,断言时留余量)。
    """
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


H = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)  # 结算窗口 [10:00, 11:00)
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
) -> tuple[int, str]:
    """直接落库实例 + 事件(合成时间戳),返回 (instance_id, uuid)。

    events 元素:(ts, from, to) 或 (ts, from, to, metadata)。
    wallet_credit=True 顺带预存 100.00(计费用例要余额);注销/列表类用例传 False。
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
            juicefs_subpath=f"disk-{user_id}-{now_utc().timestamp()}".replace(".", ""),
            price_gb_month=Decimal(price),
            status=status,
            created_at=created_at or now_utc(),
        )
        session.add(disk)
        await session.commit()
        return disk.id, disk.uuid


def prom_mock(values: list[tuple[float, float]] | None = None, *, fail: bool = False):
    """构造假 Prometheus:MockTransport 注入。"""

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
    """单行平台配置覆盖(走 set_platform_settings 的校验/加密,不裸 add PlatformSetting)。"""
    async with sm() as session:
        await set_platform_settings(session, {key: value}, updated_by=None)
        await session.commit()


def service_body(sku_id: int, **over) -> dict:
    """POST /services 的最小请求体(不开 SSH、需要 API Key、端口 8000)。"""
    body = {
        "sku_id": sku_id,
        "gpu_count": 1,
        "image_ref": "registry.superdl.local/vllm:0.11.0",
        "ssh_key_ids": [],
        "service_port": 8000,
    }
    body.update(over)
    return body


async def new_user(client: AsyncClient, sm, phone: str) -> tuple[dict[str, str], int, int, int]:
    """注册 + 充值 + 建 SKU。返回 (headers, user_id, ssh_key_id, sku_id)。"""
    headers, user_id, key_id = await create_user_with_key(client, phone)
    await fund_wallet(sm, user_id)
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
    """不落库构造 Instance(纯内存对象,供 build_pod_spec / reconciler 分支单测)。

    默认值取既有调用方的交集;差异一律经 overrides 传入。
    jupyter_token 默认按最终 uuid 现签,保证密文与 AAD 自洽。
    """
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
