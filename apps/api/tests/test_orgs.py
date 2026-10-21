"""Organizations: creation, invitations (no account enumeration, verified email, consent version,
expiry), the role matrix, leaving / removal / transfer / dissolution, the membership listeners and
the account-deletion rules."""

# pyright: reportPrivateUsage=false

from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.timeutil import now_utc
from app.modules.account.models import AccountDeletionRequest, User
from app.modules.notify.models import Notification
from app.modules.orgs import service as orgs_service
from app.modules.orgs.models import Organization, OrgInvitation, OrgMember
from tests.helpers import admin_headers, drain, user_headers_with_id

pytestmark = pytest.mark.usefixtures("fake")

CONSENT = {"consent_version": orgs_service.CONSENT_VERSION}


def org_email(n: int) -> str:
    return f"org-{n}@test.local"


async def org_user(client: AsyncClient, n: int) -> tuple[dict[str, str], int]:
    return await user_headers_with_id(client, org_email(n))


async def create_org(client: AsyncClient, headers: dict[str, str], name: str = "Acme Lab") -> Any:
    resp = await client.post("/api/v1/org", json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def invite_email(
    client: AsyncClient, headers: dict[str, str], email: str, role: str = "member"
) -> Any:
    resp = await client.post(
        "/api/v1/org/invitations", json={"email": email, "role": role}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def accept_invitation(
    client: AsyncClient, headers: dict[str, str], uuid: str, **body: str
) -> Any:
    return await client.post(
        f"/api/v1/org/invitations/received/{uuid}/accept",
        json=body or CONSENT,
        headers=headers,
    )


async def join_org(
    client: AsyncClient, owner: dict[str, str], n: int, role: str = "member"
) -> tuple[dict[str, str], int, int]:
    """Register user n, invite and accept; (headers, user_id, member_id)."""
    headers, user_id = await org_user(client, n)
    inv = await invite_email(client, owner, org_email(n), role)
    resp = await accept_invitation(client, headers, inv["uuid"])
    assert resp.status_code == 200, resp.text
    return headers, user_id, resp.json()["member_id"]


async def org_notices(sm: async_sessionmaker[AsyncSession], user_id: int) -> list[str]:
    async with sm() as session:
        return list(
            (
                await session.scalars(
                    select(Notification.title).where(
                        Notification.user_id == user_id, Notification.type == "org"
                    )
                )
            ).all()
        )


class TestCreate:
    async def test_creator_becomes_the_only_owner(self, client: AsyncClient):
        """Fails when the creator is not the owner of a one-member organization."""
        headers, _ = await org_user(client, 1)
        out = await create_org(client, headers, "  Acme   Lab ")
        assert out["role"] == "owner"
        assert out["member_count"] == 1
        assert out["org"]["name"] == "Acme Lab"

    async def test_second_organization_refused(self, client: AsyncClient):
        """Fails when a user can belong to two organizations."""
        headers, _ = await org_user(client, 1)
        await create_org(client, headers)
        resp = await client.post("/api/v1/org", json={"name": "Second"}, headers=headers)
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "orgs.alreadyMember"

    async def test_idempotency_key_replays_the_same_organization(self, client: AsyncClient):
        headers, _ = await org_user(client, 1)
        idem = {**headers, "Idempotency-Key": "org-create-1"}
        first = await client.post("/api/v1/org", json={"name": "Acme"}, headers=idem)
        second = await client.post("/api/v1/org", json={"name": "Acme"}, headers=idem)
        assert first.status_code == 201
        assert second.status_code == 200
        assert second.headers.get("X-Idempotent-Replay") == "true"
        assert second.json()["org"]["uuid"] == first.json()["org"]["uuid"]

    @pytest.mark.parametrize("name", ["https://evil.example", "a", "x@y", "<b>"])
    async def test_name_rejects_links_and_markup(self, client: AsyncClient, name: str):
        """Fails when an attacker-chosen organization name can carry a link or markup to
        invitees."""
        headers, _ = await org_user(client, 1)
        resp = await client.post("/api/v1/org", json={"name": name}, headers=headers)
        assert resp.status_code == 422

    async def test_real_name_gate(self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
        """Fails when an unverified user can create an organization while real name is
        required."""
        headers, _ = await org_user(client, 1)
        settings = get_settings()
        monkeypatch.setattr(settings, "compliance_profile", "cn")
        monkeypatch.setattr(settings, "real_name_enabled", True)
        monkeypatch.setattr(settings, "real_name_required_for_recharge", True)
        resp = await client.post("/api/v1/org", json={"name": "Acme"}, headers=headers)
        assert resp.status_code == 403
        assert resp.json()["message_key"] == "orgs.realNameRequired"


class TestInvitations:
    async def test_answer_and_list_identical_whether_account_exists(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """Fails when inviting reveals whether an account exists for an email."""
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        _, invitee_id = await org_user(client, 2)
        known = await invite_email(client, owner, org_email(2))
        unknown = await invite_email(client, owner, "nobody-here@test.local")
        assert set(known) == set(unknown)
        assert known["status"] == unknown["status"] == "pending"
        listed = (await client.get("/api/v1/org/invitations", headers=owner)).json()
        assert {i["status"] for i in listed} == {"pending"}
        await drain(sm)
        assert len(await org_notices(sm, invitee_id)) == 1

    async def test_only_the_verified_email_sees_the_invitation(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        invitee, invitee_id = await org_user(client, 2)
        other, _ = await org_user(client, 3)
        inv = await invite_email(client, owner, org_email(2))
        assert (await client.get("/api/v1/org/invitations/received", headers=other)).json() == []
        resp = await accept_invitation(client, other, inv["uuid"])
        assert resp.status_code == 404
        async with sm() as session:
            await session.execute(
                update(User).where(User.id == invitee_id).values(email_verified_at=None)
            )
            await session.commit()
        assert (await client.get("/api/v1/org/invitations/received", headers=invitee)).json() == []

    async def test_answered_invitations_mask_the_email(self, client: AsyncClient):
        """Fails when admins can read a member's full email from the invitation history."""
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        await join_org(client, owner, 2)
        pending = await invite_email(client, owner, org_email(3))
        listed = {
            i["uuid"]: i
            for i in (await client.get("/api/v1/org/invitations", headers=owner)).json()
        }
        assert listed[pending["uuid"]]["email"] == org_email(3)
        accepted = [i for i in listed.values() if i["status"] == "accepted"]
        assert [i["email"] for i in accepted] == ["o***@test.local"]

    async def test_same_email_returns_the_pending_invitation(self, client: AsyncClient):
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        first = await invite_email(client, owner, org_email(2))
        second = await invite_email(client, owner, org_email(2))
        assert first["uuid"] == second["uuid"]

    async def test_expired_cannot_be_accepted_and_can_be_reissued(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """Fails when an expired invitation still admits, or blocks a fresh one."""
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        invitee, _ = await org_user(client, 2)
        inv = await invite_email(client, owner, org_email(2))
        async with sm() as session:
            await session.execute(
                update(OrgInvitation).values(expires_at=now_utc() - timedelta(minutes=1))
            )
            await session.commit()
        assert (await accept_invitation(client, invitee, inv["uuid"])).status_code == 404
        listed = (await client.get("/api/v1/org/invitations", headers=owner)).json()
        assert listed[0]["status"] == "expired"
        again = await invite_email(client, owner, org_email(2))
        assert again["uuid"] != inv["uuid"]
        assert (await accept_invitation(client, invitee, again["uuid"])).status_code == 200

    async def test_accept_records_the_consented_disclosure_version(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """Fails when a member can join without the current disclosure or without a record of
        it."""
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        invitee, invitee_id = await org_user(client, 2)
        inv = await invite_email(client, owner, org_email(2))
        stale = await accept_invitation(client, invitee, inv["uuid"], consent_version="0")
        assert stale.status_code == 409
        assert stale.json()["message_key"] == "orgs.consentOutdated"
        assert (await accept_invitation(client, invitee, inv["uuid"])).status_code == 200
        async with sm() as session:
            member = await session.scalar(select(OrgMember).where(OrgMember.user_id == invitee_id))
        assert member is not None
        assert member.consent_version == orgs_service.CONSENT_VERSION
        assert member.role == "member"

    async def test_member_of_another_org_is_blocked(self, client: AsyncClient):
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        other_owner, _ = await org_user(client, 2)
        await create_org(client, other_owner, "Other")
        inv = await invite_email(client, owner, org_email(2))
        received = (
            await client.get("/api/v1/org/invitations/received", headers=other_owner)
        ).json()
        assert received[0]["blocked"] == "member_of_org"
        resp = await accept_invitation(client, other_owner, inv["uuid"])
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "orgs.alreadyMember"

    async def test_admins_invite_members_only(self, client: AsyncClient):
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        admin, _, _ = await join_org(client, owner, 2, role="admin")
        resp = await client.post(
            "/api/v1/org/invitations",
            json={"email": org_email(3), "role": "admin"},
            headers=admin,
        )
        assert resp.status_code == 403
        await invite_email(client, admin, org_email(3))

    async def test_member_cap(self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(get_settings(), "org_max_members", 2)
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        await join_org(client, owner, 2)
        third, _ = await org_user(client, 3)
        inv = await invite_email(client, owner, org_email(3))
        resp = await accept_invitation(client, third, inv["uuid"])
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "orgs.memberQuota"

    async def test_revoked_and_declined_cannot_be_accepted(self, client: AsyncClient):
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        invitee, _ = await org_user(client, 2)
        inv = await invite_email(client, owner, org_email(2))
        resp = await client.delete(f"/api/v1/org/invitations/{inv['uuid']}", headers=owner)
        assert resp.status_code == 204
        assert (await accept_invitation(client, invitee, inv["uuid"])).status_code == 404
        again = await invite_email(client, owner, org_email(2))
        resp = await client.post(
            f"/api/v1/org/invitations/received/{again['uuid']}/decline", headers=invitee
        )
        assert resp.status_code == 204
        assert (await accept_invitation(client, invitee, again["uuid"])).status_code == 404


class TestRoles:
    async def test_members_and_outsiders_see_no_member_data(self, client: AsyncClient):
        """Fails when a plain member or an outsider can read the member list, finance or
        invitations."""
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        member, _, _ = await join_org(client, owner, 2)
        outsider, _ = await org_user(client, 3)
        for path in ("/api/v1/org/members", "/api/v1/org/finance", "/api/v1/org/invitations"):
            assert (await client.get(path, headers=member)).status_code == 403, path
            assert (await client.get(path, headers=outsider)).status_code == 404, path
        view = (await client.get("/api/v1/org", headers=member)).json()
        assert view["role"] == "member"
        assert [a["role"] for a in view["admins"]] == ["owner"]

    async def test_owner_cannot_leave_but_members_can(self, client: AsyncClient):
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        member, _, _ = await join_org(client, owner, 2)
        resp = await client.post("/api/v1/org/leave", headers=owner)
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "orgs.ownerCannotLeave"
        resp = await client.post("/api/v1/org/leave", headers=member)
        assert resp.status_code == 200
        assert resp.json()["org"] is None

    async def test_removal_rules_and_notice(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """Fails when an admin can remove another admin or the owner, or a removed member is not
        told."""
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        admin, _, admin_mid = await join_org(client, owner, 2, role="admin")
        _, member_uid, member_mid = await join_org(client, owner, 3)
        _, _, other_admin_mid = await join_org(client, owner, 4, role="admin")
        owner_mid = (await client.get("/api/v1/org", headers=owner)).json()["member_id"]
        assert (
            await client.delete(f"/api/v1/org/members/{other_admin_mid}", headers=admin)
        ).status_code == 403
        assert (
            await client.delete(f"/api/v1/org/members/{owner_mid}", headers=admin)
        ).status_code == 409
        assert (
            await client.delete(f"/api/v1/org/members/{admin_mid}", headers=admin)
        ).status_code == 409
        assert (
            await client.delete(f"/api/v1/org/members/{member_mid}", headers=admin)
        ).status_code == 204
        assert len(await org_notices(sm, member_uid)) == 1
        assert (
            await client.delete(f"/api/v1/org/members/{other_admin_mid}", headers=owner)
        ).status_code == 204

    async def test_only_the_owner_changes_roles(self, client: AsyncClient):
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        admin, _, _ = await join_org(client, owner, 2, role="admin")
        _, _, member_mid = await join_org(client, owner, 3)
        path = f"/api/v1/org/members/{member_mid}"
        resp = await client.patch(path, json={"role": "admin"}, headers=admin)
        assert resp.status_code == 403
        resp = await client.patch(path, json={"remark": "  data team "}, headers=admin)
        assert resp.status_code == 200
        assert resp.json()["remark"] == "data team"
        assert resp.json()["label"] == "data team"
        resp = await client.patch(path, json={"role": "admin"}, headers=owner)
        assert resp.json()["role"] == "admin"
        owner_mid = (await client.get("/api/v1/org", headers=owner)).json()["member_id"]
        resp = await client.patch(
            f"/api/v1/org/members/{owner_mid}", json={"role": "member"}, headers=owner
        )
        assert resp.status_code == 409

    async def test_transfer_keeps_exactly_one_owner(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """Fails when a transfer leaves zero or two owners."""
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        member, _, member_mid = await join_org(client, owner, 2)
        resp = await client.post(
            "/api/v1/org/transfer", json={"member_id": member_mid}, headers=owner
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["role"] == "admin"
        assert (await client.get("/api/v1/org", headers=member)).json()["role"] == "owner"
        async with sm() as session:
            owners = await session.scalar(
                select(func.count()).select_from(OrgMember).where(OrgMember.role == "owner")
            )
        assert owners == 1

    async def test_dissolve_needs_the_name_and_ends_everything(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        owner, _ = await org_user(client, 1)
        await create_org(client, owner, "Acme Lab")
        member, member_uid, _ = await join_org(client, owner, 2)
        await invite_email(client, owner, org_email(3))
        resp = await client.post(
            "/api/v1/org/dissolve", json={"confirm_name": "Acme"}, headers=owner
        )
        assert resp.status_code == 400
        resp = await client.post(
            "/api/v1/org/dissolve", json={"confirm_name": "Acme Lab"}, headers=owner
        )
        assert resp.status_code == 200
        assert resp.json()["org"] is None
        assert (await client.get("/api/v1/org", headers=member)).json()["org"] is None
        assert len(await org_notices(sm, member_uid)) == 1
        async with sm() as session:
            org = await session.scalar(select(Organization))
            statuses = (await session.scalars(select(OrgInvitation.status))).all()
            members = await session.scalar(select(func.count()).select_from(OrgMember))
        assert org is not None and org.status == "dissolved"
        assert sorted(statuses) == ["accepted", "revoked"]
        assert members == 0
        await create_org(client, member, "Fresh")


class TestListeners:
    async def test_removed_listener_runs_in_the_removal_transaction(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """Fails when the membership listeners (netgw detaches the member's instances) can miss a
        removal, or a failing listener leaves the member removed anyway."""
        owner, owner_uid = await org_user(client, 1)
        await create_org(client, owner)
        _, member_uid, member_mid = await join_org(client, owner, 2)
        calls: list[tuple[int, set[int]]] = []

        async def record(_session: AsyncSession, org_id: int, user_ids: set[int]) -> None:
            calls.append((org_id, user_ids))

        async def explode(_session: AsyncSession, _org_id: int, _user_ids: set[int]) -> None:
            raise RuntimeError("listener failed")

        orgs_service.register_members_removed_listener(explode)
        try:
            async with sm() as session:
                with pytest.raises(RuntimeError):
                    await orgs_service.remove_member(session, owner_uid, member_mid)
        finally:
            orgs_service._members_removed_listeners.remove(explode)
        async with sm() as session:
            assert await orgs_service.membership_of(session, member_uid) is not None
        orgs_service.register_members_removed_listener(record)
        try:
            async with sm() as session:
                await orgs_service.remove_member(session, owner_uid, member_mid)
        finally:
            orgs_service._members_removed_listeners.remove(record)
        assert len(calls) == 1
        assert calls[0][1] == {member_uid}


class TestAccountDeletion:
    async def _request(self, client: AsyncClient, headers: dict[str, str], n: int) -> Any:
        return await client.post(
            "/api/v1/me/deletion-request",
            json={"handle": org_email(n), "reason": "no longer needed"},
            headers=headers,
        )

    async def _approve(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession], user_id: int
    ) -> Any:
        async with sm() as session:
            await session.execute(
                update(AccountDeletionRequest)
                .where(AccountDeletionRequest.user_id == user_id)
                .values(requested_at=now_utc() - timedelta(days=8))
            )
            req_id = await session.scalar(
                select(AccountDeletionRequest.id).where(AccountDeletionRequest.user_id == user_id)
            )
            await session.commit()
        admin = await admin_headers(sm, client)
        return await client.post(
            f"/api/admin/v1/deletion-requests/{req_id}/approve",
            json={"note": "approved after review"},
            headers=admin,
        )

    async def test_owner_with_members_is_refused_at_request_and_approval(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """Fails when deleting an owner would orphan an organization with members."""
        owner, owner_uid = await org_user(client, 1)
        await create_org(client, owner)
        assert (await self._request(client, owner, 1)).status_code == 201
        await join_org(client, owner, 2)
        resp = await self._approve(client, sm, owner_uid)
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "account.deletionOrgOwner"
        async with sm() as session:
            status = await session.scalar(
                select(AccountDeletionRequest.status).where(
                    AccountDeletionRequest.user_id == owner_uid
                )
            )
        assert status == "rejected"
        resp = await self._request(client, owner, 1)
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "account.deletionOrgOwner"

    async def test_owner_found_under_the_lock_is_refused(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """Fails when a transfer landing between the deletion pre-check and the removal dissolves
        an organization with members (or leaves it without an owner)."""
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        _, admin_uid, admin_mid = await join_org(client, owner, 2, role="admin")
        await join_org(client, owner, 3)
        await client.post("/api/v1/org/transfer", json={"member_id": admin_mid}, headers=owner)
        async with sm() as session:
            with pytest.raises(AppError) as exc:
                await orgs_service.drop_deleted_user(session, admin_uid)
            assert exc.value.message_key == "account.deletionOrgOwner"
            await session.rollback()
            assert await orgs_service.owned_org_other_members(session, admin_uid) == 2

    async def test_sole_owner_deletion_dissolves(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        owner, owner_uid = await org_user(client, 1)
        await create_org(client, owner)
        assert (await self._request(client, owner, 1)).status_code == 201
        assert (await self._approve(client, sm, owner_uid)).status_code == 200
        async with sm() as session:
            org = await session.scalar(select(Organization))
            assert org is not None and org.status == "dissolved"
            assert await orgs_service.membership_of(session, owner_uid) is None

    async def test_member_deletion_drops_the_membership(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        owner, _ = await org_user(client, 1)
        await create_org(client, owner)
        member, member_uid, _ = await join_org(client, owner, 2)
        assert (await self._request(client, member, 2)).status_code == 201
        assert (await self._approve(client, sm, member_uid)).status_code == 200
        members = (await client.get("/api/v1/org/members", headers=owner)).json()
        assert len(members) == 1
