"""Field-level invariants of audit rows: action/target truncated to column width, every row carries
request_id."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core import audit
from app.core.audit import ACTION_MAX_LENGTH, AuditLog
from tests.helpers import age_sms_codes, create_user_with_key

pytestmark = pytest.mark.usefixtures("fake")


class TestOverLongPath:
    async def test_long_path_does_not_poison_the_audit_gate(self, client: AsyncClient, sm):
        """A write request with a 400-character path: the audit row still lands (truncated to column
        width), the gate counter is untouched."""
        audit.reset_audit_gate()
        long_path = "/api/v1/" + "z" * 400
        for _ in range(audit.AUDIT_FAIL_CLOSED_THRESHOLD + 2):
            resp = await client.post(long_path, json={})
            assert resp.status_code == 404
        assert audit.audit_gate_open(), "the audit gate was jammed by an over-long path"

        async with sm() as session:
            rows = (
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.action.like("POST /api/v1/zzz%"))
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == audit.AUDIT_FAIL_CLOSED_THRESHOLD + 2
        assert all(len(r.action) <= ACTION_MAX_LENGTH for r in rows)


class TestFailedCredentialAttempts:
    async def test_failed_login_names_the_targeted_account_masked(self, client: AsyncClient, sm):
        """The audit row of a failed login carries the masked handle target."""
        await create_user_with_key(client, "u13800000230@test.local")
        resp = await client.post(
            "/api/v1/auth/login",
            json={"handle": "u13800000230@test.local", "password": "not-my-password"},
        )
        assert resp.status_code == 400 and resp.json()["message_key"] == "account.loginFailed"

        async with sm() as session:
            row = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "POST /api/v1/auth/login", AuditLog.result == 400
                    )
                )
            ).scalar_one()
        assert row.target == "handle:u***@test.local"
        assert "u13800000230@test.local" not in (row.target or "")
        assert row.detail == {"action": "login"}

    async def test_successful_login_target_is_the_user_id(self, client: AsyncClient, sm):
        """After success the target is overwritten with user:{id}."""
        _, user_id, _ = await create_user_with_key(client, "u13800000231@test.local")
        await age_sms_codes(sm)
        await client.post(
            "/api/v1/auth/verification-code",
            json={"handle": "u13800000231@test.local", "purpose": "login"},
        )
        resp = await client.post(
            "/api/v1/auth/login", json={"handle": "u13800000231@test.local", "code": "123456"}
        )
        assert resp.status_code == 200, resp.text
        async with sm() as session:
            row = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "POST /api/v1/auth/login", AuditLog.result == 200
                    )
                )
            ).scalar_one()
        assert row.target == f"user:{user_id}"


class TestRequestIdJoin:
    async def test_row_carries_the_request_id_from_the_response_header(
        self, client: AsyncClient, sm
    ):
        """The audit row's request_id == the X-Request-ID of that response."""
        headers, user_id, _ = await create_user_with_key(client, "u13800000220@test.local")
        resp = await client.post(
            "/api/v1/tickets",
            json={
                "category": "other",
                "subject": "help",
                "body": "please look into the instance network.",
            },
            headers={**headers, "User-Agent": "superdl-tests/1.0"},
        )
        assert resp.status_code == 201, resp.text
        request_id = resp.headers["x-request-id"]

        async with sm() as session:
            row = (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "POST /api/v1/tickets")
                )
            ).scalar_one()
        assert row.request_id == request_id
        assert row.user_agent == "superdl-tests/1.0"
        assert row.actor_type == "user" and row.actor_id == str(user_id)

    async def test_inbound_request_id_is_carried_through(self, client: AsyncClient, sm):
        """A gateway-supplied X-Request-ID is carried into the audit row."""
        headers, _, _ = await create_user_with_key(client, "u13800000221@test.local")
        resp = await client.post(
            "/api/v1/tickets",
            json={
                "category": "other",
                "subject": "help 2",
                "body": "please look into the instance disk.",
            },
            headers={**headers, "X-Request-ID": "gw-trace-abc123"},
        )
        assert resp.status_code == 201, resp.text
        async with sm() as session:
            row = (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "POST /api/v1/tickets")
                )
            ).scalar_one()
        assert row.request_id == "gw-trace-abc123"
