"""管理端 TOTP MFA:全部管理角色强制绑定,二要素登录,恢复码,重置救援。"""

import pytest
from httpx import AsyncClient

from app.core.k8s import set_orchestrator
from app.core.k8s.fake import FakeOrchestrator
from tests.test_catalog import admin_headers, complete_mfa_setup

pytestmark = pytest.mark.usefixtures("fake")


@pytest.fixture
def fake():
    orch = FakeOrchestrator(auto_ready=False)
    set_orchestrator(orch)
    yield orch
    set_orchestrator(None)


async def _login(client: AsyncClient, username: str, password: str = "pass1234"):
    return await client.post(
        "/api/admin/v1/auth/login", json={"username": username, "password": password}
    )


async def _create(client, sm, username: str, role: str) -> None:
    from app.modules.adminapi.service import create_admin

    async with sm() as session:
        await create_admin(session, username, "pass1234", role)


class TestMfaEnforcement:
    async def test_admin_role_gets_setup_challenge(self, client: AsyncClient, sm):
        await _create(client, sm, "mfa-admin", "admin")
        body = (await _login(client, "mfa-admin")).json()
        assert body["status"] == "mfa_setup"
        assert "access_token" not in body
        assert body["ticket"]

    async def test_finance_role_also_enforced(self, client: AsyncClient, sm):
        await _create(client, sm, "mfa-fin", "finance")
        assert (await _login(client, "mfa-fin")).json()["status"] == "mfa_setup"

    async def test_ops_and_readonly_also_enforced(self, client: AsyncClient, sm):
        """ops(可签节点接入令牌)与 readonly(可导出流水/审计)同样强制绑定 TOTP。"""
        await _create(client, sm, "mfa-ops", "ops")
        assert (await _login(client, "mfa-ops")).json()["status"] == "mfa_setup"
        await _create(client, sm, "mfa-ro", "readonly")
        assert (await _login(client, "mfa-ro")).json()["status"] == "mfa_setup"


class TestSetupFlow:
    async def test_begin_idempotent_same_secret(self, client: AsyncClient, sm):
        """绑定页刷新/重进看到同一二维码:重复 begin 复用进行中的密钥。"""
        await _create(client, sm, "idem-admin", "admin")
        ticket = (await _login(client, "idem-admin")).json()["ticket"]
        a = await client.post("/api/admin/v1/auth/mfa/setup/begin", json={"ticket": ticket})
        b = await client.post("/api/admin/v1/auth/mfa/setup/begin", json={"ticket": ticket})
        assert a.json()["secret"] == b.json()["secret"]
        assert a.json()["otpauth_uri"].startswith("otpauth://totp/")

    async def test_confirm_wrong_code_rejected(self, client: AsyncClient, sm):
        await _create(client, sm, "wrong-admin", "admin")
        ticket = (await _login(client, "wrong-admin")).json()["ticket"]
        await client.post("/api/admin/v1/auth/mfa/setup/begin", json={"ticket": ticket})
        resp = await client.post(
            "/api/admin/v1/auth/mfa/setup/confirm", json={"ticket": ticket, "code": "000000"}
        )
        assert resp.json()["code"] == "MFA_CODE_INVALID"

    async def test_ticket_cannot_cross_stage(self, client: AsyncClient, sm):
        """setup 票不能拿去登录验证口(typ 校验),反之亦然。"""
        await _create(client, sm, "cross-admin", "admin")
        ticket = (await _login(client, "cross-admin")).json()["ticket"]
        resp = await client.post(
            "/api/admin/v1/auth/login/mfa", json={"ticket": ticket, "code": "123456"}
        )
        assert resp.json()["code"] == "MFA_TICKET_INVALID"

    async def test_full_bind_returns_recovery_codes_once(self, client: AsyncClient, sm):
        await _create(client, sm, "full-admin", "admin")
        ticket = (await _login(client, "full-admin")).json()["ticket"]
        token = await complete_mfa_setup(client, ticket)
        me = await client.get("/api/admin/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200


class TestVerifyLogin:
    async def _bound_admin(self, client: AsyncClient, sm, username: str) -> None:
        await _create(client, sm, username, "admin")
        ticket = (await _login(client, username)).json()["ticket"]
        await complete_mfa_setup(client, ticket)

    async def test_bound_admin_gets_verify_challenge(self, client: AsyncClient, sm):
        await self._bound_admin(client, sm, "bound-admin")
        body = (await _login(client, "bound-admin")).json()
        assert body["status"] == "mfa_required"

    async def test_totp_login_success(self, client: AsyncClient, sm):
        import pyotp

        await _create(client, sm, "totp-admin", "admin")
        ticket = (await _login(client, "totp-admin")).json()["ticket"]
        begin = await client.post("/api/admin/v1/auth/mfa/setup/begin", json={"ticket": ticket})
        secret = begin.json()["secret"]
        confirm = await client.post(
            "/api/admin/v1/auth/mfa/setup/confirm",
            json={"ticket": ticket, "code": pyotp.TOTP(secret).now()},
        )
        assert confirm.status_code == 200
        codes = confirm.json()["recovery_codes"]
        assert len(codes) == 10

        # 重新登录:TOTP 直过
        ticket2 = (await _login(client, "totp-admin")).json()["ticket"]
        resp = await client.post(
            "/api/admin/v1/auth/login/mfa",
            json={"ticket": ticket2, "code": pyotp.TOTP(secret).now()},
        )
        assert resp.status_code == 200
        assert resp.json()["recovery_codes_left"] is None

        # 恢复码:用后作废,重放即拒
        ticket3 = (await _login(client, "totp-admin")).json()["ticket"]
        use = await client.post(
            "/api/admin/v1/auth/login/mfa", json={"ticket": ticket3, "code": codes[0]}
        )
        assert use.status_code == 200
        assert use.json()["recovery_codes_left"] == 9
        ticket4 = (await _login(client, "totp-admin")).json()["ticket"]
        reuse = await client.post(
            "/api/admin/v1/auth/login/mfa", json={"ticket": ticket4, "code": codes[0]}
        )
        assert reuse.json()["code"] == "MFA_CODE_INVALID"

    async def test_mfa_rate_limited_after_5_failures(self, client: AsyncClient, sm):
        await self._bound_admin(client, sm, "brute-admin")
        ticket = (await _login(client, "brute-admin")).json()["ticket"]
        for _ in range(5):
            resp = await client.post(
                "/api/admin/v1/auth/login/mfa", json={"ticket": ticket, "code": "000000"}
            )
            assert resp.json()["code"] == "MFA_CODE_INVALID"
        resp = await client.post(
            "/api/admin/v1/auth/login/mfa", json={"ticket": ticket, "code": "000000"}
        )
        assert resp.status_code == 429


class TestRecoveryRegenAndReset:
    async def test_regenerate_invalidates_old_codes(self, client: AsyncClient, sm):
        await _create(client, sm, "regen-admin", "admin")
        ticket = (await _login(client, "regen-admin")).json()["ticket"]
        begin = await client.post("/api/admin/v1/auth/mfa/setup/begin", json={"ticket": ticket})
        import pyotp

        confirm = await client.post(
            "/api/admin/v1/auth/mfa/setup/confirm",
            json={"ticket": ticket, "code": pyotp.TOTP(begin.json()["secret"]).now()},
        )
        old_codes = confirm.json()["recovery_codes"]
        token = confirm.json()["access_token"]
        regen = await client.post(
            "/api/admin/v1/me/mfa/recovery-codes", headers={"Authorization": f"Bearer {token}"}
        )
        assert regen.status_code == 200
        new_codes = regen.json()["recovery_codes"]
        assert set(new_codes) != set(old_codes)
        # 旧码已作废
        ticket2 = (await _login(client, "regen-admin")).json()["ticket"]
        stale = await client.post(
            "/api/admin/v1/auth/login/mfa", json={"ticket": ticket2, "code": old_codes[1]}
        )
        assert stale.json()["code"] == "MFA_CODE_INVALID"

    async def test_reset_by_super_admin(self, client: AsyncClient, sm):
        """锁死救援:另一位超管重置 → 绑定清空 + 会话踢掉 → 下次登录重新强制绑定。"""
        from sqlalchemy import select

        from app.modules.adminapi.models import AdminUser

        h = await admin_headers(sm, client)  # admin-user(超管)
        await _create(client, sm, "rescue-admin", "admin")
        ticket = (await _login(client, "rescue-admin")).json()["ticket"]
        victim_token = await complete_mfa_setup(client, ticket)

        async with sm() as session:
            victim_id = (
                await session.execute(
                    select(AdminUser.id).where(AdminUser.username == "rescue-admin")
                )
            ).scalar_one()
        resp = await client.post(
            f"/api/admin/v1/admins/{victim_id}/mfa/reset",
            headers=h,
            json={"reason": "验证器丢失救援"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["totp_enabled"] is False
        # 旧 token 已踢(token_version+1)
        me = await client.get(
            "/api/admin/v1/me", headers={"Authorization": f"Bearer {victim_token}"}
        )
        assert me.status_code == 401
        # 下次登录重新走绑定流
        assert (await _login(client, "rescue-admin")).json()["status"] == "mfa_setup"

    async def test_reset_self_forbidden(self, client: AsyncClient, sm):
        from sqlalchemy import select

        from app.modules.adminapi.models import AdminUser

        h = await admin_headers(sm, client)
        async with sm() as session:
            my_id = (
                await session.execute(
                    select(AdminUser.id).where(AdminUser.username == "admin-user")
                )
            ).scalar_one()
        resp = await client.post(
            f"/api/admin/v1/admins/{my_id}/mfa/reset",
            headers=h,
            json={"reason": "尝试自重置"},
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "MFA_RESET_SELF_FORBIDDEN"

    async def test_reset_requires_super_admin(self, client: AsyncClient, sm):
        h = await admin_headers(sm, client, role="ops")
        resp = await client.post(
            "/api/admin/v1/admins/999/mfa/reset", headers=h, json={"reason": "越权尝试"}
        )
        assert resp.status_code == 403
