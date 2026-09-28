"""Admin TOTP MFA: with admin_mfa_enabled every role must enrol and use the second factor,
recovery codes, reset rescue;
when off the password alone signs in."""

import asyncio
from typing import get_args

import pyotp
import pytest
from httpx import AsyncClient

from app.modules.adminapi.schemas import AdminRole
from tests.helpers import admin_headers, admin_login, complete_mfa_setup, set_platform_setting

pytestmark = pytest.mark.usefixtures("fake")


async def _create(client, sm, username: str, role: str) -> None:
    from app.modules.adminapi.auth_service import create_admin

    async with sm() as session:
        await create_admin(session, username, "pass1234", role)


class TestMfaEnforcement:
    async def test_every_role_gets_setup_challenge(self, client: AsyncClient, sm):
        """Unenrolled login returns only the enrolment ticket, no direct token, for every role."""
        for role in get_args(AdminRole):
            await _create(client, sm, f"mfa-{role}", role)
            body = (await admin_login(client, f"mfa-{role}")).json()
            assert body["status"] == "mfa_setup", (role, body)
            assert "access_token" not in body
            assert body["ticket"]

    async def test_switch_off_skips_mfa_for_everyone_and_on_restores(self, client: AsyncClient, sm):
        """admin_mfa_enabled=false: enrolled and unenrolled both get a token directly;
        once re-enabled, enrolled accounts return to the second factor and unenrolled ones to the
        enrolment ticket."""
        from sqlalchemy import delete

        from app.core.platform_config import PlatformSetting

        await _create(client, sm, "sw-bound", "admin")
        await _create(client, sm, "sw-unbound", "ops")
        await complete_mfa_setup(client, (await admin_login(client, "sw-bound")).json()["ticket"])

        await set_platform_setting(sm, "admin_mfa_enabled", "false")
        for name in ("sw-bound", "sw-unbound"):
            body = (await admin_login(client, name)).json()
            assert body["status"] == "ok", (name, body)
            me = await client.get(
                "/api/admin/v1/me", headers={"Authorization": f"Bearer {body['access_token']}"}
            )
            assert me.status_code == 200 and me.json()["username"] == name

        async with sm() as session:
            await session.execute(
                delete(PlatformSetting).where(PlatformSetting.key == "admin_mfa_enabled")
            )
            await session.commit()
        assert (await admin_login(client, "sw-bound")).json()["status"] == "mfa_required"
        assert (await admin_login(client, "sw-unbound")).json()["status"] == "mfa_setup"


class TestSetupFlow:
    async def test_concurrent_begin_returns_the_stored_secret(self, client: AsyncClient, sm):
        """Concurrent begin stores one secret and the page gets the one in the database."""
        await _create(client, sm, "race-admin", "admin")
        ticket = (await admin_login(client, "race-admin")).json()["ticket"]
        a, b = await asyncio.gather(
            client.post("/api/admin/v1/auth/mfa/setup/begin", json={"ticket": ticket}),
            client.post("/api/admin/v1/auth/mfa/setup/begin", json={"ticket": ticket}),
        )
        assert a.json()["secret"] == b.json()["secret"]
        assert a.json()["otpauth_uri"].startswith("otpauth://totp/")
        resp = await client.post(
            "/api/admin/v1/auth/mfa/setup/confirm",
            json={"ticket": ticket, "code": pyotp.TOTP(a.json()["secret"]).now()},
        )
        assert resp.status_code == 200, resp.text

    async def test_confirm_wrong_code_rejected(self, client: AsyncClient, sm):
        await _create(client, sm, "wrong-admin", "admin")
        ticket = (await admin_login(client, "wrong-admin")).json()["ticket"]
        await client.post("/api/admin/v1/auth/mfa/setup/begin", json={"ticket": ticket})
        resp = await client.post(
            "/api/admin/v1/auth/mfa/setup/confirm", json={"ticket": ticket, "code": "000000"}
        )
        assert resp.json()["code"] == "MFA_CODE_INVALID"

    async def test_bind_raises_platform_alert(self, client: AsyncClient, sm):
        """A successful TOTP enrolment writes a platform alert."""
        from sqlalchemy import select

        from app.modules.notify.models import Notification

        await _create(client, sm, "alert-admin", "admin")
        ticket = (await admin_login(client, "alert-admin")).json()["ticket"]
        await complete_mfa_setup(client, ticket)
        async with sm() as session:
            rows = (
                (
                    await session.execute(
                        select(Notification).where(Notification.type == "admin_alert")
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1
        assert "alert-admin" in rows[0].content
        assert rows[0].severity == "warning"

    async def test_setup_ticket_dies_at_bind(self, client: AsyncClient, sm):
        """A successful enrolment voids the setup ticket (token_version+1)."""
        await _create(client, sm, "onetime-admin", "admin")
        ticket = (await admin_login(client, "onetime-admin")).json()["ticket"]
        await complete_mfa_setup(client, ticket)
        again = await client.post("/api/admin/v1/auth/mfa/setup/begin", json={"ticket": ticket})
        assert again.json()["code"] == "MFA_TICKET_INVALID", again.text
        reconfirm = await client.post(
            "/api/admin/v1/auth/mfa/setup/confirm", json={"ticket": ticket, "code": "000000"}
        )
        assert reconfirm.json()["code"] == "MFA_TICKET_INVALID"

    async def test_begin_refuses_already_bound_account(self, client: AsyncClient, sm):
        """An enrolled account gets no seed again, even with a valid current-version setup
        ticket."""
        from sqlalchemy import select

        from app.core.security import create_token
        from app.modules.adminapi.models import AdminUser

        await _create(client, sm, "bound-begin-admin", "admin")
        await complete_mfa_setup(
            client, (await admin_login(client, "bound-begin-admin")).json()["ticket"]
        )
        async with sm() as session:
            admin = (
                await session.execute(
                    select(AdminUser).where(AdminUser.username == "bound-begin-admin")
                )
            ).scalar_one()
            fresh = create_token(
                str(admin.id),
                "admin",
                token_type="mfa_setup",
                ttl_seconds=600,
                extra={"ver": admin.token_version},
            )
        resp = await client.post("/api/admin/v1/auth/mfa/setup/begin", json={"ticket": fresh})
        assert resp.json()["code"] == "MFA_TICKET_INVALID", resp.text
        assert "secret" not in resp.json()

    async def test_ticket_cannot_cross_stage(self, client: AsyncClient, sm):
        """setup and login tickets are not interchangeable (typ)."""
        await _create(client, sm, "cross-admin", "admin")
        ticket = (await admin_login(client, "cross-admin")).json()["ticket"]
        resp = await client.post(
            "/api/admin/v1/auth/login/mfa", json={"ticket": ticket, "code": "123456"}
        )
        assert resp.json()["code"] == "MFA_TICKET_INVALID"


class TestVerifyLogin:
    async def _bound_admin(self, client: AsyncClient, sm, username: str) -> None:
        await _create(client, sm, username, "admin")
        ticket = (await admin_login(client, username)).json()["ticket"]
        await complete_mfa_setup(client, ticket)

    async def test_totp_login_success(self, client: AsyncClient, sm):
        import time

        import pyotp

        await _create(client, sm, "totp-admin", "admin")
        ticket = (await admin_login(client, "totp-admin")).json()["ticket"]
        begin = await client.post("/api/admin/v1/auth/mfa/setup/begin", json={"ticket": ticket})
        secret = begin.json()["secret"]
        confirm = await client.post(
            "/api/admin/v1/auth/mfa/setup/confirm",
            json={"ticket": ticket, "code": pyotp.TOTP(secret).now()},
        )
        assert confirm.status_code == 200
        codes = confirm.json()["recovery_codes"]
        assert len(codes) == 10

        ticket2 = (await admin_login(client, "totp-admin")).json()["ticket"]
        resp = await client.post(
            "/api/admin/v1/auth/login/mfa",
            json={"ticket": ticket2, "code": pyotp.TOTP(secret).at(int(time.time()) + 30)},
        )
        assert resp.status_code == 200
        assert resp.json()["recovery_codes_left"] is None

        ticket3 = (await admin_login(client, "totp-admin")).json()["ticket"]
        use = await client.post(
            "/api/admin/v1/auth/login/mfa", json={"ticket": ticket3, "code": codes[0]}
        )
        assert use.status_code == 200
        assert use.json()["recovery_codes_left"] == 9
        ticket4 = (await admin_login(client, "totp-admin")).json()["ticket"]
        reuse = await client.post(
            "/api/admin/v1/auth/login/mfa", json={"ticket": ticket4, "code": codes[0]}
        )
        assert reuse.json()["code"] == "MFA_CODE_INVALID"

    async def test_same_totp_code_replay_rejected(self, client: AsyncClient, sm):
        """Replay protection (RFC 6238 §5.2): the same code is refused the second time."""
        import pyotp

        await _create(client, sm, "replay-admin", "admin")
        ticket = (await admin_login(client, "replay-admin")).json()["ticket"]
        begin = await client.post("/api/admin/v1/auth/mfa/setup/begin", json={"ticket": ticket})
        secret = begin.json()["secret"]
        code = pyotp.TOTP(secret).now()
        confirm = await client.post(
            "/api/admin/v1/auth/mfa/setup/confirm", json={"ticket": ticket, "code": code}
        )
        assert confirm.status_code == 200
        again = await client.post(
            "/api/admin/v1/auth/mfa/setup/confirm", json={"ticket": ticket, "code": code}
        )
        assert again.json()["code"] == "MFA_TICKET_INVALID"
        ticket2 = (await admin_login(client, "replay-admin")).json()["ticket"]
        replay = await client.post(
            "/api/admin/v1/auth/login/mfa", json={"ticket": ticket2, "code": code}
        )
        assert replay.json()["code"] == "MFA_CODE_INVALID"

    async def test_mfa_rate_limited_after_5_failures(self, client: AsyncClient, sm):
        await self._bound_admin(client, sm, "brute-admin")
        ticket = (await admin_login(client, "brute-admin")).json()["ticket"]
        for _ in range(4):
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
        ticket = (await admin_login(client, "regen-admin")).json()["ticket"]
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
        ticket2 = (await admin_login(client, "regen-admin")).json()["ticket"]
        stale = await client.post(
            "/api/admin/v1/auth/login/mfa", json={"ticket": ticket2, "code": old_codes[1]}
        )
        assert stale.json()["code"] == "MFA_CODE_INVALID"

    async def test_reset_by_super_admin(self, client: AsyncClient, sm):
        """Lockout rescue: another admin resets → enrolment cleared + sessions kicked → the next
        login enrols again."""
        from sqlalchemy import select

        from app.modules.adminapi.models import AdminUser

        h = await admin_headers(sm, client)
        await _create(client, sm, "rescue-admin", "admin")
        ticket = (await admin_login(client, "rescue-admin")).json()["ticket"]
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
            json={"reason": "authenticator lost, rescue"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["totp_enabled"] is False
        me = await client.get(
            "/api/admin/v1/me", headers={"Authorization": f"Bearer {victim_token}"}
        )
        assert me.status_code == 401
        assert (await admin_login(client, "rescue-admin")).json()["status"] == "mfa_setup"

    @pytest.mark.parametrize("operation", ["reset_mfa", "logout"])
    async def test_revocation_refreshes_preloaded_version(self, client: AsyncClient, sm, operation):
        """A stale identity-map row must not overwrite an intervening revocation's version."""
        from sqlalchemy import select

        from app.modules.adminapi import auth_service
        from app.modules.adminapi.models import AdminUser

        await _create(client, sm, "revocation-actor", "admin")
        await _create(client, sm, "revocation-target", "ops")
        async with sm() as stale, sm() as intervening:
            actor = (
                await stale.execute(
                    select(AdminUser).where(AdminUser.username == "revocation-actor")
                )
            ).scalar_one()
            target = (
                await stale.execute(
                    select(AdminUser).where(AdminUser.username == "revocation-target")
                )
            ).scalar_one()
            original_version = target.token_version
            await auth_service.logout(intervening, target.id)
            assert target.token_version == original_version
            ticket = (await admin_login(client, "revocation-target")).json()["ticket"]

            if operation == "reset_mfa":
                await auth_service.reset_totp(stale, actor, target.id)
            else:
                await auth_service.logout(stale, target.id)
            await stale.refresh(target)
            assert target.token_version == original_version + 2

        response = await client.post("/api/admin/v1/auth/mfa/setup/begin", json={"ticket": ticket})
        assert response.status_code == 400
        assert response.json()["code"] == "MFA_TICKET_INVALID"

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
            json={"reason": "attempted self-reset"},
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "MFA_RESET_SELF_FORBIDDEN"
