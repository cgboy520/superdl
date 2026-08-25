"""测试共享助手:造用户/密钥/SKU/钱包,驱动 outbox 与 reconciler。"""

import base64
import secrets
import struct
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import outbox
from app.modules.billing import service as billing_service


async def drain(
    sm: async_sessionmaker[AsyncSession],
    *,
    limit: int = 100,
    task_types: frozenset[str] | None = None,
) -> int:
    """连续处理 outbox 直到队列空(或到 limit),返回处理个数。

    只是测试驱动手段:生产 worker 关停不做冲刷(SIGTERM 直接停在跑任务,遗留 running
    由 reaper 超时打回 pending)。失败任务静默滑进重试;要断言「全部成功」用 drain_strict。
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
    from tests.test_account_auth import register

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
    """按业务唯一键 (gpu_model, tier, mig_profile, gpu_cores_pct) get-or-create。

    skus 有业务键唯一约束:同一用例内多次 provisioning 复用同一条,而不是撞约束。
    同键但其余字段不同的请求直接报错(测试写法问题,不该静默复用)。
    """
    from sqlalchemy import select

    from app.modules.catalog.models import Sku
    from tests.test_catalog import make_sku

    async with sm() as session:
        wanted = make_sku(**overrides)
        existing = (
            await session.execute(
                select(Sku).where(
                    Sku.gpu_model == wanted.gpu_model,
                    Sku.tier == wanted.tier,
                    Sku.mig_profile.is_(None)
                    if wanted.mig_profile is None
                    else Sku.mig_profile == wanted.mig_profile,
                    Sku.gpu_cores_pct == wanted.gpu_cores_pct,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            same = (
                existing.pool_label == wanted.pool_label
                and existing.price_hourly == wanted.price_hourly
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
    gpu_model: str = "RTX4090",
    gpu_count: int = 32,
    gpu_used: int = 0,
    status: str = "Ready",
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
                status=status,
                last_seen=now_utc(),
            )
        )
        await session.commit()
