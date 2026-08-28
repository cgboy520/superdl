"""测试共享助手:造用户/密钥/SKU/管理员/钱包,注册与登录,驱动 outbox 与 reconciler。

跨用例复用的助手一律落在这里。测试模块之间不互相 import 助手 —— 那样为了一个
两行的登录函数会连带 import 对方整个测试类树,还会绕出 helpers ↔ 测试模块的循环。
"""

import base64
import secrets
import struct
from datetime import timedelta
from decimal import Decimal

import pyotp
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import outbox
from app.core.crypto import hash_sms_code
from app.core.timeutil import now_utc
from app.modules.account.models import SmsCode
from app.modules.adminapi.service import create_admin
from app.modules.billing import service as billing_service
from app.modules.catalog.models import PlatformImage, Sku
from app.modules.orchestrator.reconciler import reconcile_once


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


class OutboxDrainError(RuntimeError):
    """drain_strict 冲刷到未成功的任务(dead 或退避回 pending),携带 (done, failed) 计数。"""

    def __init__(self, done_count: int, failed_count: int) -> None:
        self.done_count = done_count
        self.failed_count = failed_count
        super().__init__(f"outbox drain 未全成功: done={done_count}, failed={failed_count}")


async def drain_strict(
    sm: async_sessionmaker[AsyncSession], *, limit: int = 100
) -> tuple[int, int]:
    """drain 的严格变体:返回 (done_count, failed_count);任一任务未成功
    (dead,或失败退避回 pending 等下轮)即抛 OutboxDrainError。"""
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
        raise OutboxDrainError(done, failed)
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
                gpu_model=gpu_model,
                gpu_count=gpu_count,
                gpu_used=gpu_used,
                # vCPU/内存是 CPU 档库存口径的数据源(GPU 档不看这两列)
                vcpu=vcpu,
                mem_gb=mem_gb,
                status=status,
                last_seen=now_utc(),
            )
        )
        await session.commit()


PHONE = "13800000001"


async def send_code(client: AsyncClient, phone: str = PHONE, purpose: str = "register") -> None:
    resp = await client.post(
        "/api/v1/auth/sms-code",
        # mock 渠道固定放行串(人机校验闸门;与 MOCK_SMS_CODE "123456" 同哲学)
        json={"phone": phone, "purpose": purpose},
    )
    assert resp.status_code == 204, resp.text


async def age_sms_codes(sm: async_sessionmaker[AsyncSession]) -> None:
    """把既有验证码的 created_at 回拨,越过 60s 限频窗口(不影响有效期)。"""
    async with sm() as session:
        await session.execute(update(SmsCode).values(created_at=now_utc() - timedelta(minutes=2)))
        await session.commit()


async def register(client: AsyncClient, phone: str = PHONE, password: str | None = None) -> dict:
    await send_code(client, phone, "register")
    body: dict = {"phone": phone, "sms_code": "123456", "accept_terms": True}
    if password:
        body["password"] = password
    resp = await client.post("/api/v1/auth/register", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


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
                image_ref="registry.superdl.local/pytorch:2.9.0-cu128",
            )
        )
        await session.commit()


async def complete_mfa_setup(client: AsyncClient, ticket: str) -> str:
    """mfa_setup 票 → begin → confirm(当前 TOTP)→ access token。供管理端测试复用。"""
    begin = await client.post("/api/admin/v1/auth/mfa/setup/begin", json={"ticket": ticket})
    assert begin.status_code == 200, begin.text
    secret = begin.json()["secret"]
    confirm = await client.post(
        "/api/admin/v1/auth/mfa/setup/confirm",
        json={"ticket": ticket, "code": pyotp.TOTP(secret).now()},
    )
    assert confirm.status_code == 200, confirm.text
    return confirm.json()["access_token"]


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
    resp = await client.post(
        "/api/admin/v1/auth/login", json={"username": name, "password": "pass1234"}
    )
    assert resp.status_code == 200, resp.text
    token = await complete_mfa_setup(client, resp.json()["ticket"])
    return {"Authorization": f"Bearer {token}"}


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
