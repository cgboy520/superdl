from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditLog
from app.core.errors import AppError
from app.core.security import create_token, decode_token
from app.core.timeutil import now_utc
from app.modules.account.models import SmsCode, User

PHONE = "13800000001"


async def send_code(client: AsyncClient, phone: str = PHONE, purpose: str = "register") -> None:
    resp = await client.post("/api/v1/auth/sms-code", json={"phone": phone, "purpose": purpose})
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


class TestRegister:
    async def test_register_then_me(self, client: AsyncClient):
        data = await register(client)
        assert data["user"]["phone"] == PHONE
        resp = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {data['access_token']}"}
        )
        assert resp.status_code == 200
        assert resp.json()["phone"] == PHONE

    async def test_duplicate_phone(self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]):
        await register(client)
        await age_sms_codes(sm)
        await send_code(client)
        resp = await client.post(
            "/api/v1/auth/register",
            json={"phone": PHONE, "sms_code": "123456", "accept_terms": True},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "PHONE_TAKEN"

    async def test_wrong_code(self, client: AsyncClient):
        await send_code(client)
        resp = await client.post(
            "/api/v1/auth/register",
            json={"phone": PHONE, "sms_code": "999999", "accept_terms": True},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "SMS_CODE_INVALID"

    async def test_code_single_use(self, client: AsyncClient):
        await register(client)  # 消费了验证码
        resp = await client.post(
            "/api/v1/auth/register",
            json={"phone": "13800000002", "sms_code": "123456", "accept_terms": True},
        )
        # 另一手机号没发过码
        assert resp.json()["code"] == "SMS_CODE_INVALID"

    async def test_expired_code(self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]):
        await send_code(client)
        async with sm() as session:
            await session.execute(
                update(SmsCode).values(expires_at=now_utc() - timedelta(minutes=1))
            )
            await session.commit()
        resp = await client.post(
            "/api/v1/auth/register",
            json={"phone": PHONE, "sms_code": "123456", "accept_terms": True},
        )
        assert resp.json()["code"] == "SMS_CODE_INVALID"

    async def test_sms_rate_limit(self, client: AsyncClient):
        await send_code(client)
        resp = await client.post(
            "/api/v1/auth/sms-code", json={"phone": PHONE, "purpose": "register"}
        )
        assert resp.status_code == 429
        assert resp.json()["code"] == "SMS_TOO_FREQUENT"


class TestLogin:
    async def test_login_with_password(self, client: AsyncClient):
        await register(client, password="secret123")
        resp = await client.post(
            "/api/v1/auth/login", json={"phone": PHONE, "password": "secret123"}
        )
        assert resp.status_code == 200
        assert resp.json()["access_token"]

    async def test_login_wrong_password(self, client: AsyncClient):
        await register(client, password="secret123")
        resp = await client.post("/api/v1/auth/login", json={"phone": PHONE, "password": "nope-1"})
        assert resp.json()["code"] == "LOGIN_FAILED"

    async def test_login_with_sms(self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]):
        await register(client)
        await age_sms_codes(sm)
        await send_code(client, PHONE, "login")
        resp = await client.post("/api/v1/auth/login", json={"phone": PHONE, "sms_code": "123456"})
        assert resp.status_code == 200

    async def test_frozen_user(self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]):
        await register(client, password="secret123")
        async with sm() as session:
            await session.execute(update(User).values(status="frozen"))
            await session.commit()
        resp = await client.post(
            "/api/v1/auth/login", json={"phone": PHONE, "password": "secret123"}
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "USER_FROZEN"

    async def test_refresh_flow(self, client: AsyncClient):
        data = await register(client)
        resp = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": data["refresh_token"]}
        )
        assert resp.status_code == 200
        assert resp.json()["access_token"]

    async def test_access_token_cannot_refresh(self, client: AsyncClient):
        data = await register(client)
        resp = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": data["access_token"]}
        )
        assert resp.status_code == 401


class TestAudienceIsolation:
    def test_admin_token_rejected_for_user_scope(self):
        admin_token = create_token("1", "admin")
        with pytest.raises(AppError) as exc:
            decode_token(admin_token, "user")
        assert exc.value.code == "UNAUTHORIZED"

    def test_user_token_rejected_for_admin_scope(self):
        user_token = create_token("1", "user")
        with pytest.raises(AppError):
            decode_token(user_token, "admin")


class TestAudit:
    async def test_write_operations_audited(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        await register(client)
        async with sm() as session:
            rows = (await session.execute(select(AuditLog))).scalars().all()
        actions = {r.action for r in rows}
        assert "POST /api/v1/auth/sms-code" in actions
        assert "POST /api/v1/auth/register" in actions
        reg = next(r for r in rows if r.action == "POST /api/v1/auth/register")
        assert reg.result == 201
        assert reg.target and reg.target.startswith("user:")


async def issue_code(sm, phone: str, purpose: str, code: str = "123456") -> None:
    """直接落一条验证码(绕开 60s 发送间隔;注册助手刚发过码时不能再发)。"""
    from datetime import timedelta

    from app.core.timeutil import now_utc
    from app.modules.account.models import SmsCode

    async with sm() as session:
        session.add(
            SmsCode(
                phone=phone,
                code=code,
                purpose=purpose,
                expires_at=now_utc() + timedelta(minutes=5),
            )
        )
        await session.commit()


class TestPasswordReset:
    async def test_set_then_login_with_new_password(self, client: AsyncClient, sm):
        """无密码账号也能凭验证码设密码,旧会话被撤销,新 token 立即可用。"""
        pair = await register(client, "13800000090")
        old_access = pair["access_token"]

        await issue_code(sm, "13800000090", "reset_password")
        resp = await client.post(
            "/api/v1/auth/password/reset",
            json={"phone": "13800000090", "sms_code": "123456", "new_password": "newpass123"},
        )
        assert resp.status_code == 200, resp.text
        new_pair = resp.json()

        # 旧 access token 因 token_version 变更立即失效
        assert (
            await client.get("/api/v1/me", headers={"Authorization": f"Bearer {old_access}"})
        ).status_code == 401
        assert (
            await client.get(
                "/api/v1/me", headers={"Authorization": f"Bearer {new_pair['access_token']}"}
            )
        ).status_code == 200
        # 新密码可登录
        resp = await client.post(
            "/api/v1/auth/login", json={"phone": "13800000090", "password": "newpass123"}
        )
        assert resp.status_code == 200, resp.text

    async def test_wrong_code_rejected(self, client: AsyncClient, sm):
        await register(client, "13800000091")
        await issue_code(sm, "13800000091", "reset_password")
        resp = await client.post(
            "/api/v1/auth/password/reset",
            json={"phone": "13800000091", "sms_code": "000000", "new_password": "newpass123"},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "SMS_CODE_INVALID"

    async def test_unknown_phone_needs_code_first(self, client: AsyncClient):
        """未注册手机号:先要过验证码那关,不构成「这个号存不存在」的探测口。"""
        resp = await client.post(
            "/api/v1/auth/password/reset",
            json={"phone": "13800000092", "sms_code": "123456", "new_password": "newpass123"},
        )
        assert resp.json()["code"] == "SMS_CODE_INVALID"
