"""Account deletion: request → 7-day cooling-off → cancel / execute (pre-checks + anonymisation);
afterwards access/refresh are 401, the handle is free to re-register, the ledger is kept."""

from datetime import timedelta
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.money import money_label
from app.core.timeutil import now_utc
from app.modules.account.models import AccountDeletionRequest, User
from app.modules.billing.models import BalanceLedger, Wallet
from tests.helpers import (
    admin_headers,
    age_sms_codes,
    create_user_with_key,
    current_refresh_token,
    funded_user,
    refresh_via_cookie,
    seed_disk,
    seed_instance,
)

PHONE = "u13800000060@test.local"
HANDLE = PHONE


async def _create_request(
    client: AsyncClient, headers: dict, handle: str = HANDLE, reason: str = "no longer needed"
):
    return await client.post(
        "/api/v1/me/deletion-request",
        json={"handle": handle, "reason": reason},
        headers=headers,
    )


async def _backdate_request(sm: async_sessionmaker[AsyncSession], user_id: int, days: int) -> None:
    """Move the user's deletion request requested_at back by the given days."""
    async with sm() as session:
        await session.execute(
            update(AccountDeletionRequest)
            .where(AccountDeletionRequest.user_id == user_id)
            .values(requested_at=now_utc() - timedelta(days=days))
        )
        await session.commit()


class TestCreate:
    async def test_handle_mismatch_400(self, client: AsyncClient, sm):
        headers, _, _ = await create_user_with_key(client, PHONE)
        resp = await _create_request(client, headers, handle="somebody-else@test.local")
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "account.deletionHandleMismatch"

    async def test_idempotent_returns_existing(self, client: AsyncClient, sm):
        headers, _, _ = await create_user_with_key(client, PHONE)
        first = await _create_request(client, headers, reason="first")
        assert first.status_code == 201, first.text
        assert first.json()["status"] == "pending"
        second = await _create_request(client, headers, reason="second")
        assert second.status_code == 201
        assert second.json()["id"] == first.json()["id"]
        assert second.json()["reason"] == "first"

    async def test_get_none_returns_null(self, client: AsyncClient, sm):
        headers, _, _ = await create_user_with_key(client, PHONE)
        resp = await client.get("/api/v1/me/deletion-request", headers=headers)
        assert resp.status_code == 200
        assert resp.json() is None

    async def test_get_returns_pending_with_cooldown(self, client: AsyncClient, sm):
        headers, _, _ = await create_user_with_key(client, PHONE)
        created = await _create_request(client, headers)
        assert created.status_code == 201
        resp = await client.get("/api/v1/me/deletion-request", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "pending"
        assert body["cooldown_ends_at"] > body["requested_at"]


class TestCancel:
    async def test_cancel_twice_409(self, client: AsyncClient, sm):
        headers, _, _ = await create_user_with_key(client, PHONE)
        assert (await _create_request(client, headers)).status_code == 201
        first = await client.post("/api/v1/me/deletion-request/cancel", headers=headers)
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "cancelled"
        resp = await client.post("/api/v1/me/deletion-request/cancel", headers=headers)
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "account.deletionNotCancellable"

    async def test_cancel_without_request_404(self, client: AsyncClient, sm):
        headers, _, _ = await create_user_with_key(client, PHONE)
        resp = await client.post("/api/v1/me/deletion-request/cancel", headers=headers)
        assert resp.status_code == 404

    async def test_reapply_after_cancel(self, client: AsyncClient, sm):
        """cancelled does not hold the partial unique index: a new request after cancelling."""
        headers, _, _ = await create_user_with_key(client, PHONE)
        assert (await _create_request(client, headers)).status_code == 201
        cancelled = await client.post("/api/v1/me/deletion-request/cancel", headers=headers)
        assert cancelled.status_code == 200
        resp = await _create_request(client, headers)
        assert resp.status_code == 201
        assert resp.json()["status"] == "pending"


class TestCooldown:
    async def test_approve_within_cooldown_409(self, client: AsyncClient, sm):
        headers, _, _ = await create_user_with_key(client, PHONE)
        req_id = (await _create_request(client, headers)).json()["id"]
        admin = await admin_headers(sm, client)
        resp = await client.post(
            f"/api/admin/v1/deletion-requests/{req_id}/approve",
            json={"note": "identity and resource list verified"},
            headers=admin,
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "account.deletionCooldown"
        async with sm() as session:
            req = await session.get(AccountDeletionRequest, req_id)
            assert req is not None and req.status == "pending"

    async def test_reject_not_limited_by_cooldown(self, client: AsyncClient, sm):
        headers, _, _ = await create_user_with_key(client, PHONE)
        req_id = (await _create_request(client, headers)).json()["id"]
        admin = await admin_headers(sm, client)
        resp = await client.post(
            f"/api/admin/v1/deletion-requests/{req_id}/reject",
            json={"note": "documents pending manual review"},
            headers=admin,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "rejected"
        assert resp.json()["note"] == "documents pending manual review"


class TestApproveGuards:
    async def test_running_instance_blocks(self, client: AsyncClient, sm):
        headers, user_id, _ = await create_user_with_key(client, PHONE)
        req_id = (await _create_request(client, headers)).json()["id"]
        _, uuid = await seed_instance(sm, user_id, status="running", wallet_credit=False)
        await _backdate_request(sm, user_id, days=8)
        admin = await admin_headers(sm, client)
        resp = await client.post(
            f"/api/admin/v1/deletion-requests/{req_id}/approve",
            json={"note": "identity and resource list verified"},
            headers=admin,
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["message_key"] == "account.deletionLeftovers"
        assert uuid in body["detail"]["instances"]
        async with sm() as session:
            req = await session.get(AccountDeletionRequest, req_id)
            assert req is not None and req.status == "rejected"
            assert req.note is not None and uuid in req.note

    async def test_terminal_instances_and_deleted_disk_pass(self, client: AsyncClient, sm):
        """released/failed instances and deleted data disks are terminal and not leftovers."""
        headers, user_id, _ = await create_user_with_key(client, PHONE)
        req_id = (await _create_request(client, headers)).json()["id"]
        await seed_instance(sm, user_id, status="released", wallet_credit=False)
        await seed_instance(sm, user_id, status="failed", wallet_credit=False)
        await seed_disk(sm, user_id, status="deleted")
        await _backdate_request(sm, user_id, days=8)
        admin = await admin_headers(sm, client)
        resp = await client.post(
            f"/api/admin/v1/deletion-requests/{req_id}/approve",
            json={"note": "identity and resource list verified"},
            headers=admin,
        )
        assert resp.status_code == 200, resp.text

    async def test_active_disk_blocks(self, client: AsyncClient, sm):
        headers, user_id, _ = await create_user_with_key(client, PHONE)
        req_id = (await _create_request(client, headers)).json()["id"]
        _, uuid = await seed_disk(sm, user_id, status="active")
        await _backdate_request(sm, user_id, days=8)
        admin = await admin_headers(sm, client)
        resp = await client.post(
            f"/api/admin/v1/deletion-requests/{req_id}/approve",
            json={"note": "identity and resource list verified"},
            headers=admin,
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["message_key"] == "account.deletionLeftovers"
        assert uuid in body["detail"]["disks"]

    async def test_nonzero_balance_blocks(self, client: AsyncClient, sm):
        headers, user_id, _ = await funded_user(client, sm, PHONE, "88.00")
        req_id = (await _create_request(client, headers)).json()["id"]
        await _backdate_request(sm, user_id, days=8)
        admin = await admin_headers(sm, client)
        resp = await client.post(
            f"/api/admin/v1/deletion-requests/{req_id}/approve",
            json={"note": "identity and resource list verified"},
            headers=admin,
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["message_key"] == "account.deletionBalanceRemaining"
        assert body["params"]["balance"] == money_label("88.00")
        async with sm() as session:
            req = await session.get(AccountDeletionRequest, req_id)
            assert req is not None and req.status == "rejected"
            assert req.note is not None and "88.00" in req.note and "refund" in req.note


class TestApproveSuccess:
    async def test_anonymization_and_token_revocation(self, client: AsyncClient, sm):
        headers, user_id, _ = await create_user_with_key(client, PHONE)
        async with sm() as session:
            await session.execute(
                update(User)
                .where(User.id == user_id)
                .values(
                    phone="+8613800000060",
                    kyc_name="Alice Example",
                    kyc_identity_masked="1101************12",
                    kyc_status="verified",
                )
            )
            await session.commit()
        await age_sms_codes(sm)
        await client.post(
            "/api/v1/auth/verification-code",
            json={"handle": PHONE, "purpose": "login"},
        )
        login = await client.post("/api/v1/auth/login", json={"handle": PHONE, "code": "123456"})
        assert login.status_code == 200, login.text
        old_refresh = current_refresh_token(client)
        old_access = login.json()["access_token"]

        req_id = (await _create_request(client, headers)).json()["id"]
        await _backdate_request(sm, user_id, days=8)
        admin = await admin_headers(sm, client)
        resp = await client.post(
            f"/api/admin/v1/deletion-requests/{req_id}/approve",
            json={"note": "identity and resource list verified"},
            headers=admin,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "completed"
        assert resp.json()["note"] == "identity and resource list verified"

        async with sm() as session:
            user = await session.get(User, user_id)
            assert user is not None
            assert user.email is None and user.email_verified_at is None
            assert user.phone is None
            assert user.kyc_name is None and user.kyc_identity_masked is None
            assert user.kyc_provider is None and user.kyc_verified_at is None
            assert user.kyc_status == "unverified"
            assert user.status == "deleted"

        me = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {old_access}"})
        assert me.status_code == 401
        assert me.json()["message_key"] == "account.accountDeleted"
        assert me.json()["message"] == "Account deleted"
        refresh = await refresh_via_cookie(client, old_refresh)
        assert refresh.status_code == 401
        assert refresh.json()["message_key"] == "account.accountDeleted"
        await client.post(
            "/api/v1/auth/verification-code",
            json={"handle": PHONE, "purpose": "login"},
        )
        relogin = await client.post("/api/v1/auth/login", json={"handle": PHONE, "code": "123456"})
        assert relogin.status_code == 400
        assert relogin.json()["message_key"] == "account.loginFailed"
        await age_sms_codes(sm)
        send = await client.post(
            "/api/v1/auth/verification-code",
            json={"handle": PHONE, "purpose": "register"},
        )
        assert send.status_code == 204, send.text
        reregister = await client.post(
            "/api/v1/auth/register",
            json={"email": PHONE, "email_code": "123456", "accept_terms": True},
        )
        assert reregister.status_code == 201, reregister.text
        assert reregister.json()["user"]["id"] != user_id

    async def test_deleted_handles_are_null_and_reusable(self, client: AsyncClient, sm):
        """Two deletions of the same handle both leave NULL handles (no derivable placeholder),
        and the handle can be registered again each time."""
        admin = await admin_headers(sm, client)
        ids: list[int] = []
        for _ in range(2):
            headers, user_id, _ = await create_user_with_key(client, PHONE)
            ids.append(user_id)
            req_id = (await _create_request(client, headers)).json()["id"]
            await _backdate_request(sm, user_id, days=8)
            resp = await client.post(
                f"/api/admin/v1/deletion-requests/{req_id}/approve",
                json={"note": "identity and resource list verified"},
                headers=admin,
            )
            assert resp.status_code == 200, resp.text
            async with sm() as session:
                user = await session.get(User, user_id)
                assert user is not None
                assert user.email is None and user.phone is None
            await age_sms_codes(sm)
        assert ids[0] != ids[1]

    async def test_ledger_preserved(self, client: AsyncClient, sm):
        """Deletion only anonymises the identity, balance_ledger rows are untouched."""
        headers, user_id, _ = await funded_user(client, sm, PHONE, "100.00")
        async with sm() as session:
            before = (
                await session.execute(
                    select(func.count())
                    .select_from(BalanceLedger)
                    .where(BalanceLedger.user_id == user_id)
                )
            ).scalar_one()
            assert before > 0
            await session.execute(
                update(Wallet).where(Wallet.user_id == user_id).values(balance=Decimal("0.00"))
            )
            await session.commit()
        req_id = (await _create_request(client, headers)).json()["id"]
        await _backdate_request(sm, user_id, days=8)
        admin = await admin_headers(sm, client)
        resp = await client.post(
            f"/api/admin/v1/deletion-requests/{req_id}/approve",
            json={"note": "identity and resource list verified"},
            headers=admin,
        )
        assert resp.status_code == 200, resp.text
        async with sm() as session:
            after = (
                await session.execute(
                    select(func.count())
                    .select_from(BalanceLedger)
                    .where(BalanceLedger.user_id == user_id)
                )
            ).scalar_one()
        assert after == before


class TestAdminRoles:
    async def test_admin_list_status_filter(self, client: AsyncClient, sm):
        headers, _, _ = await create_user_with_key(client, PHONE)
        assert (await _create_request(client, headers)).status_code == 201
        admin = await admin_headers(sm, client)
        pending = await client.get("/api/admin/v1/deletion-requests?status=pending", headers=admin)
        assert pending.status_code == 200
        assert len(pending.json()) == 1
        row = pending.json()[0]
        assert row["instances_active"] == 0
        assert row["disks_active"] == 0
        assert row["balance"] == "0.00"
        completed = await client.get(
            "/api/admin/v1/deletion-requests?status=completed", headers=admin
        )
        assert completed.status_code == 200
        assert completed.json() == []
