"""管理员账号 CRUD + 撤销闸。"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.k8s import set_orchestrator
from app.core.k8s.fake import FakeOrchestrator
from tests.test_catalog import admin_headers

pytestmark = pytest.mark.usefixtures("fake")

STRONG = "s3cret-passw0rd"


@pytest.fixture
def fake():
    orch = FakeOrchestrator(auto_ready=False)
    set_orchestrator(orch)
    yield orch
    set_orchestrator(None)


async def login(client: AsyncClient, username: str, password: str):
    return await client.post(
        "/api/admin/v1/auth/login", json={"username": username, "password": password}
    )


# TOTP 密钥注册表(进程级):同一账号多次 login_headers(绑定后重登录)共享密钥
_TOTP_SECRETS: dict[str, str] = {}


async def login_headers(client: AsyncClient, username: str, password: str) -> dict[str, str]:
    """登录并拿到 token:全角色强制 TOTP 后,首次绑定走 setup 流并记下密钥,
    已绑定账号走二要素验证流(密钥见 _TOTP_SECRETS)。"""
    import pyotp

    resp = await login(client, username, password)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    if body["status"] == "mfa_setup":
        ticket = body["ticket"]
        begin = await client.post("/api/admin/v1/auth/mfa/setup/begin", json={"ticket": ticket})
        assert begin.status_code == 200, begin.text
        secret = begin.json()["secret"]
        _TOTP_SECRETS[username] = secret
        confirm = await client.post(
            "/api/admin/v1/auth/mfa/setup/confirm",
            json={"ticket": ticket, "code": pyotp.TOTP(secret).now()},
        )
        assert confirm.status_code == 200, confirm.text
        token = confirm.json()["access_token"]
    else:  # mfa_required:已绑定账号的二要素登录
        # 防重放后同一枚码只能用一次:绑定已用当前步,登录用下一枚(valid_window=1 接受)
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
        """建号接口不回密码/token_version,建出来的账号能真的登录。"""
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

        # 新建的账号能登录
        assert (await login(client, "finance01", STRONG)).status_code == 200

    async def test_long_password_create_then_login(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """65~72 字符口令:创建(字节校验 ≤72B 放行)后能登录(挂了 = 登录上限 64 <
        创建上限,该区间口令的管理员被 422 永久锁死;>72 字节由 _check_password_bytes 拦在创建侧)。"""
        h = await admin_headers(sm, client)
        long_pw = "Lp" + "x9" * 34  # 70 字符 = 70 字节,创建放行、旧登录上限(64)会锁死
        resp = await client.post(
            "/api/admin/v1/admins",
            json={
                "username": "longpw01",
                "password": long_pw,
                "role": "readonly",
                "reason": "长口令回归",
            },
            headers=h,
        )
        assert resp.status_code == 201, resp.text
        assert (await login(client, "longpw01", long_pw)).status_code == 200

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
        # 停用即刻生效,不等 2 小时 TTL
        assert (await client.get("/api/admin/v1/me", headers=h2)).status_code == 401
        assert (await login(client, "ops01", STRONG)).status_code == 403

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

        # 改角色 → 旧 token 失效(权限变了,旧 token 不能继续按旧角色用)
        await client.patch(
            f"/api/admin/v1/admins/{tid}", json={"role": "readonly", "reason": "转岗"}, headers=h
        )
        assert (await client.get("/api/admin/v1/me", headers=h2)).status_code == 401

        # 重置密码 → 新密码可登录,旧密码不行
        h3 = await login_headers(client, "ops02", STRONG)
        assert (await client.get("/api/admin/v1/me", headers=h3)).status_code == 200
        resp = await client.post(
            f"/api/admin/v1/admins/{tid}/reset-password",
            json={"password": "n3w-passw0rd!", "reason": "疑似泄露"},
            headers=h,
        )
        assert resp.status_code == 200, resp.text
        assert (await client.get("/api/admin/v1/me", headers=h3)).status_code == 401
        assert (await login(client, "ops02", STRONG)).json()["code"] == "LOGIN_FAILED"
        assert (await login(client, "ops02", "n3w-passw0rd!")).status_code == 200

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
        assert (await login(client, "admin-user", "an0ther-passw0rd")).status_code == 200

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
        """自停用/自降权一律 409:最常见的一键把自己锁在门外。
        (没有「最后一个超管」保护:另一位超管可以停用你,常备第二个超管是运维纪律。)"""
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
        """require_roles() 无参(仅超管)的拒绝文案单独成键,不再是半截话「需要角色:」。"""
        h_ops = await admin_headers(sm, client, role="ops")
        resp = await client.get("/api/admin/v1/admins", headers=h_ops)
        assert resp.status_code == 403
        assert resp.json()["message_key"] == "adminapi.roleRequiredAdmin"
        # 有参分支走角色清单键
        resp = await client.post(
            "/api/admin/v1/adjustments",
            json={"user_id": 1, "amount": "1.00", "reason": "角色门验证"},
            headers=h_ops,
        )
        assert resp.status_code == 403
        assert resp.json()["message_key"] == "adminapi.roleRequired"
        assert resp.json()["params"] == {"roles": "finance"}


class TestAdminPasswordByteLimit:
    """bcrypt 上限 72 字节:schema 按字符计,多字节口令在服务层按字节拦成 400,不进哈希层炸 500。"""

    async def test_create_admin_multibyte_password(self, client, sm):
        h = await admin_headers(sm, client)
        too_long = await client.post(
            "/api/admin/v1/admins",
            json={"username": "ops-cn", "password": "汉" * 25, "role": "ops", "reason": "入职"},
            headers=h,
        )
        assert too_long.status_code == 400
        assert too_long.json()["code"] == "VALIDATION_ERROR"
        # 72 字节整(24 个汉字)可建可登录
        ok = await client.post(
            "/api/admin/v1/admins",
            json={"username": "ops-cn2", "password": "汉" * 24, "role": "ops", "reason": "入职"},
            headers=h,
        )
        assert ok.status_code == 201, ok.text
        assert (await login(client, "ops-cn2", "汉" * 24)).status_code == 200

    async def test_reset_password_multibyte_limit(self, client, sm):
        h = await admin_headers(sm, client)
        me = (await client.get("/api/admin/v1/me", headers=h)).json()
        resp = await client.post(
            f"/api/admin/v1/admins/{me['id']}/reset-password",
            json={"password": "汉" * 25, "reason": "轮换"},
            headers=h,
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "VALIDATION_ERROR"

    async def test_self_password_change_multibyte_limit(self, client, sm):
        h = await admin_headers(sm, client)
        resp = await client.post(
            "/api/admin/v1/me/password",
            json={"current_password": "pass1234", "new_password": "汉" * 25},
            headers=h,
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "VALIDATION_ERROR"
