"""Audit, MFA, ticket transaction and missing-metering regressions without external services."""

# pyright: reportPrivateUsage=false

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from fastapi import Request
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.errors import AppError, ErrorCode
from app.modules.adminapi import auth_service
from app.modules.adminapi.models import AdminUser
from app.modules.metering import service as metering
from app.modules.tickets import service as tickets
from app.modules.tickets.models import Ticket, TicketMessage


def _session() -> MagicMock:
    session = MagicMock(spec=AsyncSession)
    session.__aenter__.return_value = session
    session.execute.return_value = MagicMock()
    return session


def _request(method: str = "POST") -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/api/v1/review-regression",
            "headers": [],
            "scheme": "http",
            "server": ("test", 80),
            "query_string": b"",
        }
    )


@pytest.mark.parametrize("failure", ["insert", "commit"])
async def test_audit_probe_requires_committed_insert(monkeypatch, failure):
    """A readable database with broken audit inserts or commits cannot reopen the gate."""
    session = _session()
    session.execute.return_value = Mock()
    getattr(session, "add" if failure == "insert" else "commit").side_effect = RuntimeError(
        "audit writes unavailable"
    )
    monkeypatch.setattr(audit, "get_sessionmaker", lambda: lambda: session)
    monkeypatch.setattr(audit, "_audit_consecutive_failures", audit.AUDIT_FAIL_CLOSED_THRESHOLD)

    assert not await audit.audit_probe_ok()
    assert not audit.audit_gate_open()
    session.execute.assert_not_awaited()
    session.add.assert_called_once()


async def test_audit_probe_commits_a_real_audit_row(monkeypatch):
    session = _session()
    monkeypatch.setattr(audit, "get_sessionmaker", lambda: lambda: session)
    monkeypatch.setattr(audit, "_audit_consecutive_failures", audit.AUDIT_FAIL_CLOSED_THRESHOLD)

    assert await audit.audit_probe_ok()
    row = session.add.call_args.args[0]
    assert isinstance(row, audit.AuditLog)
    assert (row.actor_type, row.action, row.result) == ("system", "audit.recovery_probe", 200)
    session.commit.assert_awaited_once()
    assert not audit.audit_gate_open(), "Only the caller reopens the gate after probe success"


async def test_audit_gate_stays_closed_until_writes_recover(monkeypatch):
    session = _session()
    session.commit.side_effect = RuntimeError("audit commit unavailable")
    monkeypatch.setattr(audit, "get_sessionmaker", lambda: lambda: session)
    monkeypatch.setattr(audit, "_audit_consecutive_failures", audit.AUDIT_FAIL_CLOSED_THRESHOLD)
    downstream = AsyncMock()
    middleware = audit.AuditMiddleware(downstream)
    send = AsyncMock()
    receive = AsyncMock()

    for _ in range(2):
        await middleware(_request().scope, receive, send)
    downstream.assert_not_awaited()
    starts = [c.args[0] for c in send.await_args_list if c.args[0]["type"] == "http.response.start"]
    assert [m["status"] for m in starts] == [503, 503]
    assert not audit.audit_gate_open()

    await middleware(_request("GET").scope, receive, send)
    downstream.assert_awaited_once()
    assert not audit.audit_gate_open()
    session.commit.side_effect = None
    await middleware(_request().scope, receive, send)
    assert downstream.await_count == 2
    assert audit.audit_gate_open()


async def test_synchronous_audit_stays_in_business_transaction(monkeypatch):
    session = _session()
    independent_session = Mock(side_effect=AssertionError("independent audit is not allowed"))
    monkeypatch.setattr(audit, "get_sessionmaker", independent_session)
    request = _request()

    await audit.write_audit_sync(request, session)
    session.add.assert_called_once()
    session.commit.assert_not_awaited()
    assert request.state.audit_synced
    await audit._write_audit_row(request, 200)
    independent_session.assert_not_called()


def _admin(*, enrolled: bool) -> AdminUser:
    return AdminUser(
        id=7,
        username="review-admin",
        status="active",
        role="ops",
        token_version=4,
        totp_enabled=enrolled,
        totp_secret="unused-encrypted-value",
        last_totp_timestep=100,
    )


@pytest.mark.parametrize("operation", ["begin", "confirm", "totp", "recovery"])
@pytest.mark.parametrize("revocation", ["version", "disabled", "phase", "deleted"])
async def test_mfa_checks_revocation_after_lock_before_second_factor(
    monkeypatch, operation, revocation
):
    """The identity map can be stale until FOR UPDATE refreshes it; no factor may be consumed."""
    enrolled = operation in {"totp", "recovery"}
    admin = _admin(enrolled=enrolled)
    session = _session()

    async def get_admin(_model, _id, **kwargs):
        if kwargs.get("with_for_update") and kwargs.get("populate_existing"):
            if revocation == "deleted":
                return None
            if revocation == "version":
                admin.token_version += 1
            elif revocation == "disabled":
                admin.status = "disabled"
            elif revocation == "phase":
                admin.totp_enabled = not enrolled
        return admin

    session.get.side_effect = get_admin
    monkeypatch.setattr(auth_service, "decode_token", Mock(return_value={"sub": "7", "ver": 4}))
    monkeypatch.setattr(auth_service, "_check_mfa_rate", AsyncMock())
    decrypt = Mock(side_effect=AssertionError("must reject before reading the second factor"))
    consume = AsyncMock(side_effect=AssertionError("must reject before consuming recovery code"))
    issue = Mock(side_effect=AssertionError("revoked ticket must not issue access"))
    monkeypatch.setattr(auth_service, "_decrypt_totp_secret", decrypt)
    monkeypatch.setattr(auth_service, "_consume_recovery_code", consume)
    monkeypatch.setattr(auth_service, "create_token", issue)

    with pytest.raises(AppError) as exc:
        if operation == "begin":
            await auth_service.begin_totp_setup(session, "unused")
        elif operation == "confirm":
            await auth_service.confirm_totp_setup(session, "unused", "000000")
        else:
            await auth_service.verify_mfa_login(
                session, "unused", "000000" if operation == "totp" else "unused"
            )
    assert exc.value.code == ErrorCode.MFA_TICKET_INVALID
    session.get.assert_awaited_once_with(AdminUser, 7, with_for_update=True, populate_existing=True)
    session.commit.assert_not_awaited()
    decrypt.assert_not_called()
    consume.assert_not_awaited()
    issue.assert_not_called()
    assert admin.last_totp_timestep == 100


@pytest.mark.parametrize("operation", ["confirm", "totp", "recovery"])
async def test_missing_mfa_secret_is_ticket_error_not_decryption_failure(monkeypatch, operation):
    admin = _admin(enrolled=operation != "confirm")
    admin.totp_secret = None
    session = _session()
    session.get.return_value = admin
    monkeypatch.setattr(auth_service, "decode_token", Mock(return_value={"sub": "7", "ver": 4}))
    monkeypatch.setattr(auth_service, "_check_mfa_rate", AsyncMock())
    decrypt = Mock(side_effect=AssertionError("missing secret must not be decrypted"))
    consume = AsyncMock()
    monkeypatch.setattr(auth_service, "_decrypt_totp_secret", decrypt)
    monkeypatch.setattr(auth_service, "_consume_recovery_code", consume)

    with pytest.raises(AppError) as exc:
        if operation == "confirm":
            await auth_service.confirm_totp_setup(session, "unused", "000000")
        else:
            await auth_service.verify_mfa_login(
                session, "unused", "000000" if operation == "totp" else "unused"
            )
    assert exc.value.code == ErrorCode.MFA_TICKET_INVALID
    decrypt.assert_not_called()
    consume.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize("recovery", [False, True])
async def test_current_mfa_ticket_consumes_factor_and_commits_before_access(monkeypatch, recovery):
    admin = _admin(enrolled=True)
    session = _session()
    session.get.return_value = admin
    monkeypatch.setattr(auth_service, "decode_token", Mock(return_value={"sub": "7", "ver": 4}))
    monkeypatch.setattr(auth_service, "_check_mfa_rate", AsyncMock())
    counted = AsyncMock()
    monkeypatch.setattr(auth_service, "_count_mfa_attempt", counted)
    monkeypatch.setattr(auth_service, "_decrypt_totp_secret", Mock(return_value="unused"))
    monkeypatch.setattr(auth_service, "_match_totp_timestep", Mock(return_value=101))
    consume = AsyncMock(return_value=True)
    monkeypatch.setattr(auth_service, "_consume_recovery_code", consume)

    def issue(*_args, **_kwargs):
        session.commit.assert_awaited_once()
        return "unused"

    monkeypatch.setattr(auth_service, "create_token", issue)
    _, result, left = await auth_service.verify_mfa_login(
        session, "unused", "unused" if recovery else "000000"
    )
    assert result is admin
    assert admin.last_totp_timestep == (100 if recovery else 101)
    assert left == (0 if recovery else None)
    assert consume.await_count == int(recovery)
    counted.assert_awaited_once_with(7)
    session.get.assert_awaited_once_with(AdminUser, 7, with_for_update=True, populate_existing=True)


async def test_current_setup_ticket_binds_and_revokes_in_same_commit(monkeypatch):
    admin = _admin(enrolled=False)
    session = _session()
    session.get.return_value = admin
    monkeypatch.setattr(auth_service, "decode_token", Mock(return_value={"sub": "7", "ver": 4}))
    monkeypatch.setattr(auth_service, "_check_mfa_rate", AsyncMock())
    monkeypatch.setattr(auth_service, "_count_mfa_attempt", AsyncMock())
    monkeypatch.setattr(auth_service, "_decrypt_totp_secret", Mock(return_value="unused"))
    monkeypatch.setattr(auth_service, "_match_totp_timestep", Mock(return_value=101))
    monkeypatch.setattr(auth_service, "_gen_plain_recovery_codes", Mock(return_value=[]))
    monkeypatch.setattr(auth_service, "_hash_recovery_codes", AsyncMock(return_value=[]))

    async def notify(*_args, **_kwargs):
        session.commit.assert_not_awaited()
        assert admin.totp_enabled
        assert admin.token_version == 5
        return True

    monkeypatch.setattr(auth_service.notify_service, "notify", notify)
    monkeypatch.setattr(auth_service, "create_token", Mock(return_value="unused"))
    _, result, _ = await auth_service.confirm_totp_setup(session, "unused", "000000")
    assert result is admin
    assert admin.last_totp_timestep == 101
    session.commit.assert_awaited_once()


@pytest.mark.parametrize("alert_fails", [False, True])
async def test_ticket_creation_commits_only_after_alert(monkeypatch, alert_fails):
    session = _session()
    session.execute.return_value.scalar_one.return_value = 0
    monkeypatch.setattr(tickets, "check_rate_limit", AsyncMock())
    monkeypatch.setattr(tickets, "find_replay", AsyncMock(return_value=None))
    monkeypatch.setattr(tickets, "next_daily_seq", AsyncMock(return_value=1))

    async def insert(_session, row, **_kwargs):
        row.id = 12
        return row

    async def alert(_session, **kwargs):
        session.commit.assert_not_awaited()
        assert kwargs["dedup_key"] == "ticket:created:12"
        message = session.add.call_args.args[0]
        assert isinstance(message, TicketMessage)
        assert message.ticket_id == 12
        if alert_fails:
            raise RuntimeError("admin alert failed")

    monkeypatch.setattr(tickets, "insert_idempotent", insert)
    notify = AsyncMock(side_effect=alert)
    monkeypatch.setattr(tickets, "_admin_alert", notify)
    kwargs: dict = {
        "category": "other",
        "subject": "review ticket",
        "body": "first message",
        "instance_uuid": None,
        "idempotency_key": "review-ticket",
    }
    if alert_fails:
        with pytest.raises(RuntimeError, match="admin alert failed"):
            await tickets.create_ticket(session, 3, **kwargs)
        session.commit.assert_not_awaited()
    else:
        ticket, created = await tickets.create_ticket(session, 3, **kwargs)
        assert created and ticket.id == 12
        session.commit.assert_awaited_once()
    notify.assert_awaited_once()


@pytest.mark.parametrize("alert_fails", [False, True])
async def test_ticket_reply_commits_status_message_and_alert_together(monkeypatch, alert_fails):
    session = _session()
    session.execute.return_value.scalar_one.return_value = 1
    ticket = Ticket(id=12, ticket_no="T20260101-01", subject="review", status="open")
    monkeypatch.setattr(tickets, "check_rate_limit", AsyncMock())
    monkeypatch.setattr(tickets, "_get_my_for_update", AsyncMock(return_value=ticket))

    async def flush():
        session.add.call_args.args[0].id = 31

    async def alert(_session, **kwargs):
        session.flush.assert_awaited_once()
        session.commit.assert_not_awaited()
        assert ticket.status == "pending_staff"
        assert kwargs["dedup_key"] == "ticket:user-reply:31"
        if alert_fails:
            raise RuntimeError("admin alert failed")

    session.flush.side_effect = flush
    notify = AsyncMock(side_effect=alert)
    monkeypatch.setattr(tickets, "_admin_alert", notify)
    if alert_fails:
        with pytest.raises(RuntimeError, match="admin alert failed"):
            await tickets.append_message(session, 3, 12, body="reply")
        session.commit.assert_not_awaited()
    else:
        message = await tickets.append_message(session, 3, 12, body="reply")
        assert message.id == 31
        session.commit.assert_awaited_once()
    notify.assert_awaited_once()


async def test_ticket_replay_has_no_extra_message_or_alert(monkeypatch):
    session = _session()
    existing = Ticket(id=12)
    monkeypatch.setattr(tickets, "find_replay", AsyncMock(return_value=existing))
    quota = AsyncMock()
    alert = AsyncMock()
    monkeypatch.setattr(tickets, "check_rate_limit", quota)
    monkeypatch.setattr(tickets, "_admin_alert", alert)

    assert await tickets.create_ticket(
        session,
        3,
        category="other",
        subject="review",
        body="first message",
        instance_uuid=None,
        idempotency_key="review-ticket",
    ) == (existing, False)
    quota.assert_not_awaited()
    alert.assert_not_awaited()
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


def _aggregation_session(monkeypatch, values):
    session = _session()
    lock = MagicMock()
    lock.__aenter__ = AsyncMock(return_value=True)
    lock.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(metering, "advisory_lock", Mock(return_value=lock))
    monkeypatch.setattr(
        metering.orchestrator_queries,
        "billing_candidates",
        AsyncMock(return_value=[(1, 2, Decimal("1.0000"), 1)]),
    )
    monkeypatch.setattr(
        metering.orchestrator_queries,
        "instance_locations",
        AsyncMock(return_value={1: ("tenant-2", "instance-1", "kata")}),
    )
    monkeypatch.setattr(metering.prom, "query_instance_metric", AsyncMock(return_value=values))
    return session


async def test_empty_metering_samples_do_not_insert_or_overwrite(monkeypatch):
    session = _aggregation_session(monkeypatch, [])
    assert (
        await metering.aggregate_previous_hour(
            Mock(return_value=session), at=datetime(2026, 1, 1, 1, tzinfo=UTC)
        )
        == 0
    )
    session.execute.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize("samples, average", [([0.0], 0.0), ([20.0, 60.0], 40.0)])
async def test_valid_metering_samples_only_fill_null_aggregates(monkeypatch, samples, average):
    session = _aggregation_session(monkeypatch, list(enumerate(samples)))
    assert (
        await metering.aggregate_previous_hour(
            Mock(return_value=session), at=datetime(2026, 1, 1, 1, tzinfo=UTC)
        )
        == 1
    )
    session.commit.assert_awaited_once()
    statement = session.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert compiled.params["gpu_util_avg"] == average
    sql = str(compiled)
    assert "ON CONFLICT (instance_id, hour_start) DO UPDATE SET gpu_util_avg =" in sql
    assert "WHERE usage_hourly.gpu_util_avg IS NULL" in sql


async def test_reconciliation_excludes_missing_hours_but_not_zero(monkeypatch):
    session = _session()
    session.execute.return_value.tuples.return_value.all.return_value = [(1, 1)]
    monkeypatch.setattr(
        metering.billing_service, "billed_by_instance", AsyncMock(return_value={1: Decimal("2.00")})
    )
    monkeypatch.setattr(
        metering.orchestrator_queries,
        "instance_hourly_prices",
        AsyncMock(return_value={1: Decimal("2.0000")}),
    )
    report = await metering.reconciliation_report(session, datetime(2026, 1, 1, tzinfo=UTC))
    sql = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "usage_hourly.gpu_util_avg IS NOT NULL" in sql
    assert "gpu_util_avg >" not in sql and "gpu_util_avg !=" not in sql
    assert report.estimated_total == "2.00"
    assert report.diff_pct == 0
