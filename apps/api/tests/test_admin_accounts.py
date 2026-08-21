"""管理员账号 CRUD + 撤销闸。

没有这一组端点时,生产库开箱就是空的 admin_users 表:控制台不可登录;而调账强制双人
复核(复核人 ≠ 发起人),单账号意味着任何调账单都永远无法通过复核,财务补偿在生产上是
死锁的;审计的 actor_id 也全部指向同一个账号,追溯不到人。
"""

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


async def login_headers(client: AsyncClient, username: str, password: str) -> dict[str, str]:
    resp = await login(client, username, password)
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


class TestAdminAccounts:
    async def test_create_list_and_second_reviewer_unblocks_adjustment(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """建出第二个账号后,调账双人复核才有可能通过 —— 这正是缺 CRUD 时的死锁点。"""
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

        # 新账号能登录,并且能作为第二复核人
        second = await login(client, "finance01", STRONG)
        assert second.status_code == 200
        h2 = {"Authorization": f"Bearer {second.json()['access_token']}"}
        from tests.helpers import create_user_with_key

        _uh, user_id, _k = await create_user_with_key(client, "13700000001")
        adj = await client.post(
            "/api/admin/v1/adjustments",
            json={"user_id": user_id, "amount": "10.00", "reason": "客诉补偿"},
            headers=h,
        )
        assert adj.status_code == 201, adj.text
        # 发起人自己复核:仍然拒绝(风控本身是对的)
        self_review = await client.post(
            f"/api/admin/v1/adjustments/{adj.json()['id']}/review",
            json={"approve": True},
            headers=h,
        )
        assert self_review.json()["code"] == "ADMIN_SECOND_REVIEW_REQUIRED"
        # 第二个人复核:通过
        ok = await client.post(
            f"/api/admin/v1/adjustments/{adj.json()['id']}/review",
            json={"approve": True},
            headers=h2,
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["status"] == "approved"

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

    async def test_cannot_lock_yourself_or_the_platform_out(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        h = await admin_headers(sm, client)
        me = (await client.get("/api/admin/v1/me", headers=h)).json()
        # 自己停用自己:最常见的一键锁死
        resp = await client.patch(
            f"/api/admin/v1/admins/{me['id']}",
            json={"status": "disabled", "reason": "手滑"},
            headers=h,
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "adminapi.cannotChangeSelf"
        # 由另一个超管来停用最后一个超管:同样要挡(否则平台再也进不去管理台)
        other = await client.post(
            "/api/admin/v1/admins",
            json={"username": "root2", "password": STRONG, "role": "admin", "reason": "备用超管"},
            headers=h,
        )
        h2 = await login_headers(client, "root2", STRONG)
        assert (
            await client.patch(
                f"/api/admin/v1/admins/{me['id']}",
                json={"status": "disabled", "reason": "清理"},
                headers=h2,
            )
        ).status_code == 200  # 还剩 root2,可以停
        last = await client.patch(
            f"/api/admin/v1/admins/{other.json()['id']}",
            json={"role": "readonly", "reason": "降权"},
            headers=h2,
        )
        assert last.status_code == 409
        assert last.json()["message_key"] == "adminapi.cannotChangeSelf"

    async def test_non_admin_roles_cannot_manage_accounts(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        h_ops = await admin_headers(sm, client, role="ops")
        assert (await client.get("/api/admin/v1/admins", headers=h_ops)).status_code == 403
        resp = await client.post(
            "/api/admin/v1/admins",
            json={"username": "x1", "password": STRONG, "role": "ops", "reason": "提权"},
            headers=h_ops,
        )
        assert resp.status_code == 403
