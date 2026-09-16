"""Email-primary auth: register / login (code or password) / reset, verification-code lifecycle
(wrong, expired, burned, backoff, quotas), handle binding, compliance-profile phone rules."""

# pyright: reportPrivateUsage=false
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditLog
from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.core.handles import Handle
from app.core.security import create_token, decode_token
from app.core.timeutil import now_utc
from app.modules.account import verification
from app.modules.account.models import User, VerificationCode
from tests.helpers import (
    age_sms_codes,
    issue_code,
    refresh_via_cookie,
    register,
    send_code,
)

EMAIL = "u13800000001@test.local"
PHONE = "+8613800000001"


def _register_body(email: str, code: str = "123456", **extra: object) -> dict:
    return {"email": email, "email_code": code, "accept_terms": True, **extra}


async def _register_with_phone(client: AsyncClient, sm, email: str, phone: str) -> dict:
    await issue_code(sm, email, "register")
    await issue_code(sm, phone, "register")
    resp = await client.post(
        "/api/v1/auth/register", json=_register_body(email, phone=phone, phone_code="123456")
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestRegister:
    async def test_register_then_me(self, client: AsyncClient):
        data = await register(client)
        user = data["user"]
        assert user["email"] == EMAIL and user["email_verified_at"] is not None
        assert user["phone"] is None and user["kyc_status"] == "unverified"
        resp = await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {data['access_token']}"}
        )
        assert resp.status_code == 200
        assert resp.json()["email"] == EMAIL

    async def test_email_is_normalized(self, client: AsyncClient):
        """Mixed-case / padded input registers and logs in as the lower-cased address."""
        await send_code(client, "MiXed@Test.LOCAL", "register")
        resp = await client.post(
            "/api/v1/auth/register", json=_register_body("  MiXed@Test.LOCAL ")
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["user"]["email"] == "mixed@test.local"

    async def test_password_byte_boundary(self, client: AsyncClient):
        """Passwords are capped at 72 bytes: 24 CJK characters register, 25 → 422."""
        a, b = "u13800000071@test.local", "u13800000072@test.local"
        await send_code(client, a, "register")
        ok = await client.post(
            "/api/v1/auth/register",
            json=_register_body(a, password="汉" * 24),  # cjk-ok
        )  # cjk-ok
        assert ok.status_code == 201, ok.text
        await send_code(client, b, "register")
        too_long = await client.post(
            "/api/v1/auth/register",
            json=_register_body(b, password="汉" * 25),  # cjk-ok
        )
        assert too_long.status_code == 422
        assert too_long.json()["code"] == "VALIDATION_ERROR"

    async def test_login_accepts_every_registrable_password_length(self, client: AsyncClient):
        password = "p" * 72
        email = "u13800000073@test.local"
        await register(client, email, password=password)
        resp = await client.post("/api/v1/auth/login", json={"handle": email, "password": password})
        assert resp.status_code == 200, resp.text
        resp = await client.post(
            "/api/v1/auth/login", json={"handle": email, "password": "p" * 129}
        )
        assert resp.status_code == 422

    async def test_duplicate_email(self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]):
        await register(client)
        await age_sms_codes(sm)
        await send_code(client)
        resp = await client.post("/api/v1/auth/register", json=_register_body(EMAIL))
        assert resp.status_code == 400
        assert resp.json()["code"] == "HANDLE_TAKEN"
        assert resp.json()["message_key"] == "account.emailTaken"

    async def test_wrong_code(self, client: AsyncClient):
        await send_code(client)
        resp = await client.post("/api/v1/auth/register", json=_register_body(EMAIL, "999999"))
        assert resp.status_code == 400
        assert resp.json()["code"] == "CODE_INVALID"

    async def test_expired_code(self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]):
        await send_code(client)
        async with sm() as session:
            await session.execute(
                update(VerificationCode).values(expires_at=now_utc() - timedelta(minutes=1))
            )
            await session.commit()
        resp = await client.post("/api/v1/auth/register", json=_register_body(EMAIL))
        assert resp.json()["code"] == "CODE_INVALID"

    async def test_register_with_optional_phone_binds_it(self, client: AsyncClient, sm):
        """Generic profile: a phone may be bound at sign-up when its own SMS code is supplied."""
        data = await _register_with_phone(client, sm, EMAIL, PHONE)
        assert data["user"]["phone"] == PHONE and data["user"]["email"] == EMAIL

    async def test_phone_without_its_code_is_rejected(self, client: AsyncClient, sm):
        await issue_code(sm, EMAIL, "register")
        resp = await client.post("/api/v1/auth/register", json=_register_body(EMAIL, phone=PHONE))
        assert resp.status_code == 400
        assert resp.json()["code"] == "CODE_INVALID"

    async def test_duplicate_phone_rejected(self, client: AsyncClient, sm):
        await _register_with_phone(client, sm, EMAIL, PHONE)
        other = "u13800000002@test.local"
        await issue_code(sm, other, "register")
        await issue_code(sm, PHONE, "register")
        resp = await client.post(
            "/api/v1/auth/register", json=_register_body(other, phone=PHONE, phone_code="123456")
        )
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "account.phoneTaken"


class TestComplianceProfilePhoneRules:
    async def test_cn_profile_requires_a_plus86_phone(self, client: AsyncClient, sm, monkeypatch):
        monkeypatch.setattr(get_settings(), "compliance_profile", "cn")
        await issue_code(sm, EMAIL, "register")
        resp = await client.post("/api/v1/auth/register", json=_register_body(EMAIL))
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "account.phoneRequired"
        await issue_code(sm, "+14155550123", "register")
        resp = await client.post(
            "/api/v1/auth/register",
            json=_register_body(EMAIL, phone="+14155550123", phone_code="123456"),
        )
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "account.phoneRegionNotAllowed"
        assert resp.json()["params"] == {"codes": "+86"}
        data = await _register_with_phone(client, sm, EMAIL, PHONE)
        assert data["user"]["phone"] == PHONE

    async def test_generic_profile_accepts_any_region(self, client: AsyncClient, sm):
        data = await _register_with_phone(client, sm, EMAIL, "+14155550123")
        assert data["user"]["phone"] == "+14155550123"


class TestLogin:
    async def test_login_with_password(self, client: AsyncClient):
        await register(client, password="secret123456")
        resp = await client.post(
            "/api/v1/auth/login", json={"handle": EMAIL, "password": "secret123456"}
        )
        assert resp.status_code == 200
        assert resp.json()["access_token"]

    async def test_login_failure_is_indistinguishable(self, client: AsyncClient):
        """Unknown account and wrong password produce byte-identical bodies (minus request_id)."""
        await register(client, password="secret123456")
        registered = await client.post(
            "/api/v1/auth/login", json={"handle": EMAIL, "password": "wrong-pass"}
        )
        unknown = await client.post(
            "/api/v1/auth/login", json={"handle": "nobody@test.local", "password": "wrong-pass"}
        )
        assert registered.status_code == unknown.status_code
        r, u = registered.json(), unknown.json()
        r.pop("request_id")
        u.pop("request_id")
        assert r == u
        bad_code_registered = await client.post(
            "/api/v1/auth/login", json={"handle": EMAIL, "code": "000000"}
        )
        bad_code_unknown = await client.post(
            "/api/v1/auth/login", json={"handle": "nobody2@test.local", "code": "000000"}
        )
        br, bu = bad_code_registered.json(), bad_code_unknown.json()
        br.pop("request_id")
        bu.pop("request_id")
        assert br == bu

    async def test_login_with_code(self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]):
        await register(client)
        await age_sms_codes(sm)
        await send_code(client, EMAIL, "login")
        resp = await client.post("/api/v1/auth/login", json={"handle": EMAIL, "code": "123456"})
        assert resp.status_code == 200

    async def test_login_by_bound_phone_handle(self, client: AsyncClient, sm):
        """A bound phone is a full login handle (existing phone-first accounts keep working)."""
        await _register_with_phone(client, sm, EMAIL, PHONE)
        await issue_code(sm, PHONE, "login")
        resp = await client.post("/api/v1/auth/login", json={"handle": PHONE, "code": "123456"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["user"]["email"] == EMAIL

    async def test_bare_digits_are_not_a_handle(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/auth/login", json={"handle": "13800000001", "password": "x"}
        )
        assert resp.status_code == 422

    async def test_frozen_user(self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]):
        await register(client, password="secret123456")
        async with sm() as session:
            await session.execute(update(User).values(status="frozen"))
            await session.commit()
        resp = await client.post(
            "/api/v1/auth/login", json={"handle": EMAIL, "password": "secret123456"}
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "USER_FROZEN"

    async def test_access_token_cannot_refresh(self, client: AsyncClient):
        data = await register(client)
        resp = await refresh_via_cookie(client, data["access_token"])
        assert resp.status_code == 401

    async def test_failure_counter_reset_then_lock_blocks_even_correct_password(
        self, client: AsyncClient
    ):
        """Only failures count and one success clears; a full bucket rejects the right password."""
        email = "u13800000082@test.local"
        await register(client, email, password="secret123456")
        for _ in range(4):
            resp = await client.post(
                "/api/v1/auth/login", json={"handle": email, "password": "wrong-pass"}
            )
            assert resp.json()["code"] == "LOGIN_FAILED"
        ok = await client.post(
            "/api/v1/auth/login", json={"handle": email, "password": "secret123456"}
        )
        assert ok.status_code == 200, ok.text
        for _ in range(5):
            resp = await client.post(
                "/api/v1/auth/login", json={"handle": email, "password": "wrong-pass"}
            )
            assert resp.json()["code"] == "LOGIN_FAILED"
        resp = await client.post(
            "/api/v1/auth/login", json={"handle": email, "password": "secret123456"}
        )
        assert resp.status_code == 429
        assert resp.json()["code"] == "RATE_LIMITED"


class TestHandles:
    async def _headers(self, client: AsyncClient) -> dict[str, str]:
        data = await register(client)
        return {"Authorization": f"Bearer {data['access_token']}"}

    async def test_bind_phone_then_remove(self, client: AsyncClient, sm):
        headers = await self._headers(client)
        resp = await client.post("/api/v1/me/handles/code", json={"handle": PHONE}, headers=headers)
        assert resp.status_code == 204, resp.text
        resp = await client.post(
            "/api/v1/me/handles/confirm", json={"handle": PHONE, "code": "123456"}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["phone"] == PHONE
        await issue_code(sm, PHONE, "login")
        login = await client.post("/api/v1/auth/login", json={"handle": PHONE, "code": "123456"})
        assert login.status_code == 200
        removed = await client.delete("/api/v1/me/handles/phone", headers=headers)
        assert removed.status_code == 200 and removed.json()["phone"] is None

    async def test_change_email(self, client: AsyncClient, sm):
        headers = await self._headers(client)
        new = "new-address@test.local"
        await issue_code(sm, new, "bind_handle")
        resp = await client.post(
            "/api/v1/me/handles/confirm", json={"handle": new, "code": "123456"}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["email"] == new and resp.json()["email_verified_at"] is not None
        async with sm() as session:
            assert (await session.execute(select(User).where(User.email == EMAIL))).first() is None

    async def test_handle_owned_by_another_account_is_refused(self, client: AsyncClient, sm):
        other = "u13800000002@test.local"
        await register(client, other)
        headers = await self._headers(client)
        await issue_code(sm, other, "bind_handle")
        resp = await client.post(
            "/api/v1/me/handles/confirm", json={"handle": other, "code": "123456"}, headers=headers
        )
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "account.handleTaken"

    async def test_remove_phone_refused_under_cn_or_without_email(
        self, client: AsyncClient, sm, monkeypatch
    ):
        data = await _register_with_phone(client, sm, EMAIL, PHONE)
        headers = {"Authorization": f"Bearer {data['access_token']}"}
        monkeypatch.setattr(get_settings(), "compliance_profile", "cn")
        resp = await client.delete("/api/v1/me/handles/phone", headers=headers)
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "account.phoneRequiredByProfile"
        monkeypatch.setattr(get_settings(), "compliance_profile", None)
        async with sm() as session:
            await session.execute(update(User).values(email=None, email_verified_at=None))
            await session.commit()
        resp = await client.delete("/api/v1/me/handles/phone", headers=headers)
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "account.emailRequiredFirst"

    async def test_bind_code_respects_profile_dial_codes(
        self, client: AsyncClient, sm, monkeypatch
    ):
        headers = await self._headers(client)
        monkeypatch.setattr(get_settings(), "compliance_profile", "cn")
        resp = await client.post(
            "/api/v1/me/handles/code", json={"handle": "+14155550123"}, headers=headers
        )
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "account.phoneRegionNotAllowed"


class TestAudienceIsolation:
    def test_admin_token_rejected_for_user_scope(self):
        admin_token = create_token("1", "admin")
        with pytest.raises(AppError) as exc:
            decode_token(admin_token, "user")
        assert exc.value.code == "UNAUTHORIZED"


class TestAudit:
    async def test_write_operations_audited(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        await register(client)
        async with sm() as session:
            rows = (await session.execute(select(AuditLog))).scalars().all()
        actions = {r.action for r in rows}
        assert "POST /api/v1/auth/verification-code" in actions
        assert "POST /api/v1/auth/register" in actions
        reg = next(r for r in rows if r.action == "POST /api/v1/auth/register")
        assert reg.result == 201
        assert reg.target and reg.target.startswith("user:")


class TestPasswordReset:
    async def test_set_then_login_with_new_password(self, client: AsyncClient, sm):
        """A password-less account sets one by code; old sessions die, the new pair works."""
        email = "u13800000090@test.local"
        pair = await register(client, email)
        old_access = pair["access_token"]
        await issue_code(sm, email, "reset_password")
        resp = await client.post(
            "/api/v1/auth/password/reset",
            json={"handle": email, "code": "123456", "new_password": "newpass123456"},
        )
        assert resp.status_code == 200, resp.text
        new_pair = resp.json()
        assert (
            await client.get("/api/v1/me", headers={"Authorization": f"Bearer {old_access}"})
        ).status_code == 401
        assert (
            await client.get(
                "/api/v1/me", headers={"Authorization": f"Bearer {new_pair['access_token']}"}
            )
        ).status_code == 200
        resp = await client.post(
            "/api/v1/auth/login", json={"handle": email, "password": "newpass123456"}
        )
        assert resp.status_code == 200, resp.text

    async def test_unknown_handle_needs_code_first(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/auth/password/reset",
            json={"handle": "ghost@test.local", "code": "123456", "new_password": "newpass123456"},
        )
        assert resp.json()["code"] == "CODE_INVALID"


class TestCodeQuotaAndBackoff:
    async def test_unconsumed_sends_do_not_burn_victim_daily_quota(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """Codes requested on someone's behalf do not eat their 10/day consume quota."""
        email = "u13800000096@test.local"
        async with sm() as session:
            for _ in range(10):
                session.add(
                    VerificationCode(
                        channel="email",
                        target=email,
                        code_hash="0" * 64,
                        purpose="register",
                        expires_at=now_utc() + timedelta(minutes=5),
                        created_at=now_utc() - timedelta(hours=2),
                    )
                )
            await session.commit()
        resp = await client.post(
            "/api/v1/auth/verification-code", json={"handle": email, "purpose": "register"}
        )
        assert resp.status_code == 204, resp.text
        resp = await client.post("/api/v1/auth/register", json=_register_body(email))
        assert resp.status_code == 201, resp.text

    async def test_send_backoff_escalates_on_unconsumed_codes(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """Consecutive unconsumed codes double the interval: 60 s → 120 s."""
        email = "u13800000097@test.local"
        await send_code(client, email)
        resp = await client.post(
            "/api/v1/auth/verification-code", json={"handle": email, "purpose": "register"}
        )
        assert resp.status_code == 429
        assert resp.json()["code"] == "CODE_TOO_FREQUENT"
        assert resp.json()["params"]["seconds"] <= 60
        async with sm() as session:
            await session.execute(
                update(VerificationCode)
                .where(VerificationCode.target == email)
                .values(created_at=now_utc() - timedelta(seconds=61))
            )
            await session.commit()
        resp = await client.post(
            "/api/v1/auth/verification-code", json={"handle": email, "purpose": "register"}
        )
        assert resp.status_code == 204, resp.text
        resp = await client.post(
            "/api/v1/auth/verification-code", json={"handle": email, "purpose": "register"}
        )
        assert resp.status_code == 429
        assert 60 < resp.json()["params"]["seconds"] <= 120

    async def test_consume_quota_counts_only_successful_reads(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """The daily quota is charged on successful consumption only."""
        from app.core.crypto import hash_verification_code

        email = "u13800000098@test.local"
        handle = Handle("email", email)
        codes = [f"{200000 + i}" for i in range(11)]
        async with sm() as session:
            for code in codes:
                session.add(
                    VerificationCode(
                        channel="email",
                        target=email,
                        code_hash=hash_verification_code("email", email, "login", code),
                        purpose="login",
                        expires_at=now_utc() + timedelta(minutes=5),
                    )
                )
            await session.commit()
        async with sm() as session:
            with pytest.raises(AppError):
                await verification.consume_code(session, handle, "999999", "login")
        for code in reversed(codes[1:]):
            async with sm() as session:
                await verification.consume_code(session, handle, code, "login")
                await session.commit()
        async with sm() as session:
            with pytest.raises(AppError) as exc:
                await verification.consume_code(session, handle, codes[0], "login")
            assert exc.value.code == ErrorCode.RATE_LIMITED

    async def test_channel_follows_the_handle(self, client: AsyncClient, sm):
        """An email handle stores an email-channel row, a phone handle an sms-channel row."""
        await send_code(client, EMAIL)
        await send_code(client, PHONE)
        async with sm() as session:
            rows = (await session.execute(select(VerificationCode))).scalars().all()
        assert {(r.channel, r.target) for r in rows} == {("email", EMAIL), ("sms", PHONE)}


class TestLoginBurst:
    async def test_concurrent_burst_cannot_exceed_pair_bucket(self, client: AsyncClient):
        """Concurrency cannot amplify the IP+handle bucket: at most 5 reach bcrypt, the rest 429."""
        import asyncio

        email = "u13800000083@test.local"
        await register(client, email, password="secret123456")
        bodies = [{"handle": email, "password": "wrong-pass"} for _ in range(11)]
        bodies.append({"handle": email, "password": "secret123456"})
        responses = await asyncio.gather(
            *(client.post("/api/v1/auth/login", json=b) for b in bodies)
        )
        verified = [r for r in responses if r.status_code != 429]
        assert len(verified) <= 5
        assert sum(1 for r in responses if r.json().get("code") == "RATE_LIMITED") >= 7
        assert all(r.status_code == 200 or r.json()["code"] == "LOGIN_FAILED" for r in verified)

    async def test_success_refunds_precount_but_keeps_failures(self, client: AsyncClient):
        from app.core.ratelimit import read_hits

        email = "u13800000084@test.local"
        await register(client, email, password="secret123456")
        for _ in range(2):
            await client.post(
                "/api/v1/auth/login", json={"handle": email, "password": "bad-pass-1"}
            )
        ok = await client.post(
            "/api/v1/auth/login", json={"handle": email, "password": "secret123456"}
        )
        assert ok.status_code == 200, ok.text
        assert await read_hits(f"user-login-acct-daily:{email}", window_seconds=86400.0) == 2


class TestCodeBackoffHardening:
    async def test_burning_a_code_does_not_reset_backoff(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        """Five wrong attempts void the code; an immediate resend is still 429 (backoff counts
        unconsumed codes, not used_at)."""
        email = "u13800000085@test.local"
        await send_code(client, email)
        for _ in range(5):
            resp = await client.post("/api/v1/auth/register", json=_register_body(email, "000000"))
            assert resp.status_code == 400, resp.text
        async with sm() as session:
            row = (
                await session.execute(
                    select(VerificationCode).where(VerificationCode.target == email)
                )
            ).scalar_one()
            assert row.used_at is not None and row.consumed_at is None
        resp = await client.post(
            "/api/v1/auth/verification-code", json={"handle": email, "purpose": "register"}
        )
        assert resp.status_code == 429
        assert resp.json()["code"] == "CODE_TOO_FREQUENT"

    async def test_base_interval_applies_after_consumed_code(
        self, client: AsyncClient, sm: async_sessionmaker[AsyncSession]
    ):
        email = "u13800000086@test.local"
        await register(client, email)
        resp = await client.post(
            "/api/v1/auth/verification-code", json={"handle": email, "purpose": "login"}
        )
        assert resp.status_code == 429
        assert resp.json()["params"]["seconds"] <= 60
        async with sm() as session:
            await session.execute(
                update(VerificationCode)
                .where(VerificationCode.target == email)
                .values(created_at=now_utc() - timedelta(seconds=61))
            )
            await session.commit()
        resp = await client.post(
            "/api/v1/auth/verification-code", json={"handle": email, "purpose": "login"}
        )
        assert resp.status_code == 204, resp.text

    async def test_target_daily_send_cap(self, client: AsyncClient):
        """The per-target daily send cap is independent of the consume quota."""
        from app.core.ratelimit import check_rate_limit

        email = "u13800000087@test.local"
        for _ in range(verification.SEND_TARGET_DAILY_MAX):
            await check_rate_limit(
                f"code-send-target:{email}",
                max_attempts=verification.SEND_TARGET_DAILY_MAX,
                window_seconds=86400.0,
            )
        resp = await client.post(
            "/api/v1/auth/verification-code", json={"handle": email, "purpose": "register"}
        )
        assert resp.status_code == 429
        assert resp.json()["code"] == "RATE_LIMITED"

    @pytest.mark.usefixtures("sm")
    async def test_notify_budget_is_separate_from_verify(self):
        from app.core.ratelimit import check_rate_limit
        from app.core.sms import SMS_PLATFORM_LIMITS, ensure_sms_platform_quota

        hourly, _ = SMS_PLATFORM_LIMITS["verify"]
        for _ in range(hourly):
            await check_rate_limit(
                "sms-platform:verify:hourly", max_attempts=hourly, window_seconds=3600.0
            )
        with pytest.raises(AppError):
            await ensure_sms_platform_quota("verify")
        await ensure_sms_platform_quota("notify")
