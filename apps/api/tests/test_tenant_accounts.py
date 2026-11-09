"""Accounts a tenant creates and balance its admins move: username sign-in, one-time passwords,
forced password change, sign-in switch, deletion rules, the identity-verification exemption,
spend-only transfers and the guards around ownership, leaving and dissolving."""

from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.modules.billing import wallet
from tests.helpers import admin_headers, fund_wallet, set_platform_setting
from tests.test_orgs import create_org, join_org, org_user

USERS = "/api/v1/org/users"


async def _tenant(client: AsyncClient) -> tuple[dict[str, str], int]:
    owner, owner_uid = await org_user(client, 1)
    await create_org(client, owner, code="ailab")
    return owner, owner_uid


async def _create(
    client: AsyncClient, owner: dict[str, str], *names: str, **body: Any
) -> list[dict[str, Any]]:
    resp = await client.post(USERS, json={"names": list(names), **body}, headers=owner)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _login(client: AsyncClient, handle: str, password: str) -> Any:
    return await client.post("/api/v1/auth/login", json={"handle": handle, "password": password})


def _bearer(resp: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _member_id(client: AsyncClient, owner: dict[str, str], username: str) -> int:
    members = (await client.get("/api/v1/org/members", headers=owner)).json()
    return next(m["member_id"] for m in members if m["username"] == username)


class TestCreateAndSignIn:
    async def test_batch_with_random_passwords_signs_in_and_must_change(self, client):
        owner, _ = await _tenant(client)
        created = await _create(client, owner, "student01", "student02")
        assert [c["username"] for c in created] == ["ailab.student01", "ailab.student02"]
        assert created[0]["password"] != created[1]["password"]
        assert len(created[0]["password"]) == 16
        members = (await client.get("/api/v1/org/members", headers=owner)).json()
        managed = [m for m in members if m["managed"]]
        assert {m["label"] for m in managed} == {"ailab.student01", "ailab.student02"}
        login = await _login(client, "AILAB.Student01", created[0]["password"])
        assert login.status_code == 200, login.text
        me = (await client.get("/api/v1/me", headers=_bearer(login))).json()
        assert me["managed"] is True and me["password_change_required"] is True
        assert me["username"] == "ailab.student01"
        changed = await client.post(
            "/api/v1/me/password",
            json={"current_password": created[0]["password"], "new_password": "my-own-pass-123"},
            headers=_bearer(login),
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["user"]["password_change_required"] is False
        stale = await _login(client, "ailab.student01", created[0]["password"])
        assert stale.json()["code"] == "LOGIN_FAILED"

    async def test_shared_password_duplicates_and_taken_names(self, client):
        owner, _ = await _tenant(client)
        created = await _create(client, owner, "a1", "a2", password="shared-pass-1234")
        assert {c["password"] for c in created} == {"shared-pass-1234"}
        dup = await client.post(USERS, json={"names": ["b1", "b1"]}, headers=owner)
        assert dup.json()["message_key"] == "orgs.usernamesDuplicate"
        taken = await client.post(USERS, json={"names": ["a1", "c1"]}, headers=owner)
        assert taken.status_code == 409
        assert taken.json()["message_key"] == "orgs.usernamesTaken"

    async def test_members_cannot_create_and_usernames_get_no_codes(self, client):
        owner, _ = await _tenant(client)
        member, _uid, _ = await join_org(client, owner, 2)
        resp = await client.post(USERS, json={"names": ["x"]}, headers=member)
        assert resp.status_code == 403
        code = await client.post(
            "/api/v1/auth/verification-code",
            json={"handle": "ailab.x", "purpose": "login"},
        )
        assert code.json()["message_key"] == "account.usernameNoCode"

    async def test_managed_accounts_skip_identity_verification(
        self, client, sm, monkeypatch: pytest.MonkeyPatch
    ):
        owner, _ = await _tenant(client)
        created = await _create(client, owner, "lab")
        monkeypatch.setattr(get_settings(), "compliance_profile", "cn")
        await set_platform_setting(sm, "real_name_enabled", "true")
        await set_platform_setting(sm, "real_name_required_for_recharge", "true")
        login = await _login(client, "ailab.lab", created[0]["password"])
        recharge = await client.post(
            "/api/v1/wallet/recharges",
            json={"amount": "50.00", "channel": "mock"},
            headers=_bearer(login),
        )
        assert recharge.status_code == 201, recharge.text


class TestAdminControls:
    async def test_reset_disable_and_owner_rules(self, client):
        owner, _ = await _tenant(client)
        created = await _create(client, owner, "s1")
        mid = await _member_id(client, owner, "ailab.s1")
        session = await _login(client, "ailab.s1", created[0]["password"])
        reset = await client.post(f"/api/v1/org/members/{mid}/password", json={}, headers=owner)
        assert reset.status_code == 200, reset.text
        assert (await client.get("/api/v1/me", headers=_bearer(session))).status_code == 401
        assert (await _login(client, "ailab.s1", reset.json()["password"])).status_code == 200
        off = await client.patch(
            f"/api/v1/org/members/{mid}/login", json={"login_disabled": True}, headers=owner
        )
        assert off.json()["login_disabled"] is True
        refused = await _login(client, "ailab.s1", reset.json()["password"])
        assert refused.json()["message_key"] == "account.loginDisabled"
        to_owner = await client.post("/api/v1/org/transfer", json={"member_id": mid}, headers=owner)
        assert to_owner.json()["message_key"] == "orgs.ownerMustBeOwnAccount"
        removal = await client.delete(f"/api/v1/org/members/{mid}", headers=owner)
        assert removal.json()["message_key"] == "orgs.managedUseDelete"

    async def test_managed_cannot_leave_and_tenant_cannot_dissolve(self, client):
        owner, _ = await _tenant(client)
        created = await _create(client, owner, "s1")
        session = await _login(client, "ailab.s1", created[0]["password"])
        leave = await client.post("/api/v1/org/leave", headers=_bearer(session))
        assert leave.json()["message_key"] == "orgs.managedCannotLeave"
        dissolve = await client.post(
            "/api/v1/org/dissolve", json={"confirm_name": "Acme Lab"}, headers=owner
        )
        assert dissolve.json()["message_key"] == "orgs.dissolveHasManagedUsers"
        code = await client.patch("/api/v1/org", json={"code": "other"}, headers=owner)
        assert code.json()["message_key"] == "orgs.codeLocked"


class TestBalanceMoves:
    async def test_allocate_reclaim_and_delete_an_empty_account(
        self, client, sm: async_sessionmaker[AsyncSession]
    ):
        owner, owner_uid = await _tenant(client)
        await fund_wallet(sm, owner_uid, "100.00")
        created = await _create(client, owner, "s1")
        mid = await _member_id(client, owner, "ailab.s1")
        member_uid = created[0]["user_id"]
        idem = {**owner, "Idempotency-Key": "alloc-1"}
        body = {"kind": "allocate", "amount": "30.00"}
        first = await client.post(f"/api/v1/org/members/{mid}/balance", json=body, headers=idem)
        again = await client.post(f"/api/v1/org/members/{mid}/balance", json=body, headers=idem)
        assert first.status_code == 200, first.text
        assert again.headers.get("X-Idempotent-Replay") == "true"
        async with sm() as session:
            assert await wallet.get_balance(session, owner_uid) == Decimal("70.00")
            assert await wallet.get_balance(session, member_uid) == Decimal("30.00")
            assert await wallet.refundable_capacity(session, member_uid) == Decimal("0.00")
        blocked = await client.delete(f"/api/v1/org/members/{mid}/account", headers=owner)
        assert blocked.json()["message_key"] == "orgs.managedHasBalance"
        too_much = await client.post(
            f"/api/v1/org/members/{mid}/balance",
            json={"kind": "reclaim", "amount": "31.00"},
            headers=owner,
        )
        assert too_much.json()["code"] == "INSUFFICIENT_BALANCE"
        back = await client.post(
            f"/api/v1/org/members/{mid}/balance",
            json={"kind": "reclaim", "amount": "30.00"},
            headers=owner,
        )
        assert back.status_code == 200, back.text
        async with sm() as session:
            # The payer can refund what came back; the account never could.
            assert await wallet.refundable_capacity(session, owner_uid) == Decimal("100.00")
            assert await wallet.refundable_capacity(session, member_uid) == Decimal("0.00")
        moves = (await client.get("/api/v1/org/transfers", headers=owner)).json()
        assert [m["kind"] for m in moves] == ["reclaim", "allocate"]
        gone = await client.delete(f"/api/v1/org/members/{mid}/account", headers=owner)
        assert gone.status_code == 204, gone.text
        assert (await _login(client, "ailab.s1", created[0]["password"])).json()[
            "code"
        ] == "LOGIN_FAILED"

    async def test_reclaim_only_from_accounts_the_tenant_created(self, client, sm):
        owner, owner_uid = await _tenant(client)
        await fund_wallet(sm, owner_uid, "50.00")
        _member, member_uid, mid = await join_org(client, owner, 2)
        await fund_wallet(sm, member_uid, "20.00")
        resp = await client.post(
            f"/api/v1/org/members/{mid}/balance",
            json={"kind": "reclaim", "amount": "5.00"},
            headers=owner,
        )
        assert resp.json()["message_key"] == "orgs.reclaimNotManaged"
        gift = await client.post(
            f"/api/v1/org/members/{mid}/balance",
            json={"kind": "allocate", "amount": "5.00"},
            headers=owner,
        )
        assert gift.status_code == 200, gift.text
        async with sm() as session:
            assert await wallet.refundable_capacity(session, member_uid) == Decimal("20.00")


class TestAdminView:
    async def test_admins_find_created_accounts_by_username(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """Fails when support cannot look up an account that has no email or phone."""
        owner, _ = await _tenant(client)
        created = await _create(client, owner, "carol")
        admin = await admin_headers(sm, client, "readonly")
        page = (
            await client.get("/api/admin/v1/tenants", params={"q": "AILAB.carol"}, headers=admin)
        ).json()
        assert [(u["id"], u["username"]) for u in page["items"]] == [
            (created[0]["user_id"], "ailab.carol")
        ]
