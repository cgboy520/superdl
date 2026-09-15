"""管理员账号 CRUD + 撤销闸。"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.helpers import admin_headers, admin_login, complete_mfa_setup_with_secret

pytestmark = pytest.mark.usefixtures("fake")

STRONG = "s3cret-passw0rd"


async def _create_admin(sm: async_sessionmaker[AsyncSession], username: str) -> None:
    from app.modules.adminapi.auth_service import create_admin

    async with sm() as session:
        await create_admin(session, username, "pass1234", "admin")


_TOTP_SECRETS: dict[str, str] = {}


async def login_headers(client: AsyncClient, username: str, password: str) -> dict[str, str]:
    """返回管理端认证 headers:首次绑定走 setup 流并记下密钥,已绑定账号走二要素验证流。"""
    import pyotp

    resp = await admin_login(client, username, password)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    if body["status"] == "mfa_setup":
        token, secret = await complete_mfa_setup_with_secret(client, body["ticket"])
        _TOTP_SECRETS[username] = secret
    else:
        import time

        verify = await client.post(
            "/api/admin/v1/auth/login/mfa",
            json={
                "ticket": body["ticket"],
                "code": pyotp.TOTP(_TOTP_SECRETS[username]).at(int(time.time()) + 30),
            },
        )
        assert verify.status_code == 200, verify.text
        token = verify.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


class TestAdminAccounts:
    async def test_create_list_and_login(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """建号接口不回密码/token_version,建出来的账号能登录。"""
        h = await admin_headers(sm, client)
        resp = await client.post(
            "/api/admin/v1/admins",
            json={
                "username": "finance01",
                "password": STRONG,
                "role": "finance",
                "reason": "组建财务",
            },
            headers=h,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["role"] == "finance"
        assert "password" not in resp.json() and "token_version" not in resp.json()

        listing = (await client.get("/api/admin/v1/admins", headers=h)).json()
        assert {a["username"] for a in listing} == {"admin-user", "finance01"}

        assert (await admin_login(client, "finance01", STRONG)).status_code == 200

    async def test_username_conflict_is_409_not_500(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        h = await admin_headers(sm, client)
        body = {"username": "dup01", "password": STRONG, "role": "ops", "reason": "x1"}
        assert (await client.post("/api/admin/v1/admins", json=body, headers=h)).status_code == 201
        again = await client.post("/api/admin/v1/admins", json=body, headers=h)
        assert again.status_code == 409
        assert again.json()["code"] == "CONFLICT"

    async def test_disable_revokes_token_immediately(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        h = await admin_headers(sm, client)
        created = await client.post(
            "/api/admin/v1/admins",
            json={"username": "ops01", "password": STRONG, "role": "ops", "reason": "入职"},
            headers=h,
        )
        target_id = created.json()["id"]
        h2 = await login_headers(client, "ops01", STRONG)
        assert (await client.get("/api/admin/v1/me", headers=h2)).status_code == 200

        resp = await client.patch(
            f"/api/admin/v1/admins/{target_id}",
            json={"status": "disabled", "reason": "离职"},
            headers=h,
        )
        assert resp.status_code == 200, resp.text
        assert (await client.get("/api/admin/v1/me", headers=h2)).status_code == 401
        assert (await admin_login(client, "ops01", STRONG)).status_code == 403

    async def test_role_change_and_reset_password_bump_token_version(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        h = await admin_headers(sm, client)
        created = await client.post(
            "/api/admin/v1/admins",
            json={"username": "ops02", "password": STRONG, "role": "ops", "reason": "入职"},
            headers=h,
        )
        tid = created.json()["id"]
        h2 = await login_headers(client, "ops02", STRONG)

        await client.patch(
            f"/api/admin/v1/admins/{tid}", json={"role": "readonly", "reason": "转岗"}, headers=h
        )
        assert (await client.get("/api/admin/v1/me", headers=h2)).status_code == 401

        h3 = await login_headers(client, "ops02", STRONG)
        assert (await client.get("/api/admin/v1/me", headers=h3)).status_code == 200
        resp = await client.post(
            f"/api/admin/v1/admins/{tid}/reset-password",
            json={"password": "n3w-passw0rd!", "reason": "疑似泄露"},
            headers=h,
        )
        assert resp.status_code == 200, resp.text
        assert (await client.get("/api/admin/v1/me", headers=h3)).status_code == 401
        assert (await admin_login(client, "ops02", STRONG)).json()["code"] == "LOGIN_FAILED"
        assert (await admin_login(client, "ops02", "n3w-passw0rd!")).status_code == 200

    async def test_self_password_change_revokes_other_sessions(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        h = await admin_headers(sm, client)
        resp = await client.post(
            "/api/admin/v1/me/password",
            json={"current_password": "pass1234", "new_password": "an0ther-passw0rd"},
            headers=h,
        )
        assert resp.status_code == 204, resp.text
        assert (await client.get("/api/admin/v1/me", headers=h)).status_code == 401
        assert (await admin_login(client, "admin-user", "an0ther-passw0rd")).status_code == 200

    async def test_self_password_change_requires_current(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        h = await admin_headers(sm, client)
        resp = await client.post(
            "/api/admin/v1/me/password",
            json={"current_password": "wrong", "new_password": "an0ther-passw0rd"},
            headers=h,
        )
        assert resp.json()["code"] == "LOGIN_FAILED"

    async def test_cannot_disable_or_demote_yourself(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """自停用/自降权一律 409;无「最后一个超管」保护。"""
        h = await admin_headers(sm, client)
        me = (await client.get("/api/admin/v1/me", headers=h)).json()
        for body in (
            {"status": "disabled", "reason": "手滑"},
            {"role": "readonly", "reason": "降权"},
        ):
            resp = await client.patch(f"/api/admin/v1/admins/{me['id']}", json=body, headers=h)
            assert resp.status_code == 409, body
            assert resp.json()["message_key"] == "adminapi.cannotChangeSelf"

    async def test_require_roles_no_arg_has_own_message(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """require_roles() 无参(仅超管)的拒绝文案单独成键。"""
        h_ops = await admin_headers(sm, client, role="ops")
        resp = await client.get("/api/admin/v1/admins", headers=h_ops)
        assert resp.status_code == 403
        assert resp.json()["message_key"] == "adminapi.roleRequiredAdmin"
        resp = await client.post(
            "/api/admin/v1/adjustments",
            json={"user_id": 1, "amount": "1.00", "reason": "角色门验证"},
            headers=h_ops,
        )
        assert resp.status_code == 403
        assert resp.json()["message_key"] == "adminapi.roleRequired"
        assert resp.json()["params"] == {"roles": "finance"}


class TestAdminLoginLockout:
    async def test_daily_account_bucket_never_locks_out_the_real_password(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession], monkeypatch
    ):
        """日窗账号桶只计数,不封禁:打满之后正确口令仍能登录。"""
        from app.modules.adminapi import auth_service

        monkeypatch.setattr(auth_service, "LOGIN_ACCT_DAILY_MAX_ATTEMPTS", 3)
        monkeypatch.setattr(auth_service, "LOGIN_IP_MAX_ATTEMPTS", 10_000)
        monkeypatch.setattr(auth_service, "LOGIN_MAX_ATTEMPTS", 10_000)
        monkeypatch.setattr(auth_service, "LOGIN_ACCT_MAX_ATTEMPTS", 10_000)
        await _create_admin(sm, "lockout-admin")

        for _ in range(4):
            resp = await client.post(
                "/api/admin/v1/auth/login",
                json={"username": "lockout-admin", "password": "wrong-password"},
            )
            assert resp.status_code in (400, 429), resp.text
        blocked = await client.post(
            "/api/admin/v1/auth/login",
            json={"username": "lockout-admin", "password": "wrong-password"},
        )
        assert blocked.status_code == 429
        ok = await admin_login(client, "lockout-admin")
        assert ok.status_code == 200, ok.text
        assert ok.json()["status"] == "mfa_setup"


class TestAdminPasswordByteLimit:
    """多字节口令在请求体层按字节拦 72 上限(422,与用户端 PasswordStr 同一校验)。"""

    async def test_create_admin_multibyte_password(self, client, sm):
        h = await admin_headers(sm, client)
        too_long = await client.post(
            "/api/admin/v1/admins",
            json={"username": "ops-cn", "password": "汉" * 25, "role": "ops", "reason": "入职"},
            headers=h,
        )
        assert too_long.status_code == 422
        assert too_long.json()["code"] == "VALIDATION_ERROR"
        ok = await client.post(
            "/api/admin/v1/admins",
            json={"username": "ops-cn2", "password": "汉" * 24, "role": "ops", "reason": "入职"},
            headers=h,
        )
        assert ok.status_code == 201, ok.text
        assert (await admin_login(client, "ops-cn2", "汉" * 24)).status_code == 200

    async def test_reset_password_multibyte_limit(self, client, sm):
        h = await admin_headers(sm, client)
        me = (await client.get("/api/admin/v1/me", headers=h)).json()
        resp = await client.post(
            f"/api/admin/v1/admins/{me['id']}/reset-password",
            json={"password": "汉" * 25, "reason": "轮换"},
            headers=h,
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "VALIDATION_ERROR"

    async def test_self_password_change_multibyte_limit(self, client, sm):
        h = await admin_headers(sm, client)
        resp = await client.post(
            "/api/admin/v1/me/password",
            json={"current_password": "pass1234", "new_password": "汉" * 25},
            headers=h,
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "VALIDATION_ERROR"
