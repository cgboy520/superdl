"""测试共享助手:造用户/密钥/SKU/钱包,驱动 outbox 与 reconciler。"""

import base64
import secrets
import struct
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.billing import service as billing_service


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
    from tests.test_catalog import make_sku

    async with sm() as session:
        sku = make_sku(**overrides)
        session.add(sku)
        await session.commit()
        return sku.id
