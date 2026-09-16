"""Email channel seam: SMTP message/connection shape, failure mapping, factory credential checks,
verification templates per locale, platform quota, and the admin test-email endpoint."""

import pytest
from httpx import AsyncClient

from app.core import email as email_module
from app.core.email import EmailError, MockEmailChannel, SmtpEmailChannel, build_email_channel
from app.core.platform_config import runtime_config_from_strings as rc
from app.core.verification import code_email, sms_text
from tests.helpers import admin_headers


@pytest.fixture(autouse=True)
def _reset_channel():
    yield
    email_module.set_email_channel(None)


class TestSmtpChannel:
    def _channel(self, security: str = "starttls") -> SmtpEmailChannel:
        return SmtpEmailChannel(
            "smtp.example.com",
            587,
            security,  # type: ignore[arg-type]
            username="mailer",
            password="pw",
            sender="SuperDL <no-reply@example.com>",
            reply_to="support@example.com",
        )

    def test_message_carries_headers_text_and_html(self):
        msg = self._channel().build_message("a@example.com", "Subj", "plain", "<p>plain</p>")
        assert msg["From"] == "SuperDL <no-reply@example.com>" and msg["To"] == "a@example.com"
        assert msg["Subject"] == "Subj" and msg["Reply-To"] == "support@example.com"
        parts = [p.get_content_type() for p in msg.iter_parts()]
        assert parts == ["text/plain", "text/html"]

    @pytest.mark.parametrize(
        "security, use_tls, start_tls",
        [("starttls", False, True), ("tls", True, False), ("none", False, False)],
    )
    def test_connection_kwargs_follow_security_mode(self, security, use_tls, start_tls):
        kwargs = self._channel(security).connect_kwargs()
        assert (kwargs["use_tls"], kwargs["start_tls"]) == (use_tls, start_tls)
        assert (kwargs["hostname"], kwargs["port"], kwargs["username"]) == (
            "smtp.example.com",
            587,
            "mailer",
        )

    async def test_send_passes_message_and_maps_failures(self, monkeypatch):
        calls: list[tuple] = []

        async def fake_send(message, **kwargs):
            calls.append((message["To"], kwargs["hostname"], kwargs["start_tls"]))

        monkeypatch.setattr(email_module.aiosmtplib, "send", fake_send)
        await self._channel().send("a@example.com", "s", "t")
        assert calls == [("a@example.com", "smtp.example.com", True)]

        async def failing_send(message, **kwargs):
            raise email_module.aiosmtplib.SMTPException("535 auth failed")

        monkeypatch.setattr(email_module.aiosmtplib, "send", failing_send)
        with pytest.raises(EmailError, match="535"):
            await self._channel().send("a@example.com", "s", "t")


class TestFactory:
    def test_mock_smtp_and_missing_config(self):
        assert isinstance(build_email_channel(rc({"email_provider": "mock"})), MockEmailChannel)
        with pytest.raises(EmailError, match="smtp_host"):
            build_email_channel(rc({"email_provider": "smtp"}))
        ch = build_email_channel(
            rc(
                {
                    "email_provider": "smtp",
                    "smtp_host": "smtp.example.com",
                    "smtp_port": "465",
                    "smtp_security": "tls",
                    "email_from": "no-reply@example.com",
                }
            )
        )
        assert isinstance(ch, SmtpEmailChannel)
        assert ch.connect_kwargs()["port"] == 465 and ch.connect_kwargs()["use_tls"] is True
        assert "username" not in ch.connect_kwargs()


class TestTemplates:
    def test_code_email_per_locale_and_unknown_purpose(self):
        en = code_email("register", "123456")
        assert en.subject == "Confirm your SuperDL sign-up" and "123456" in en.text
        assert "<strong>123456</strong>" in en.html
        zh = code_email("reset_password", "654321", "zh-CN")
        assert zh.subject == "重置 SuperDL 密码" and "654321" in zh.text  # cjk-ok
        assert code_email("unknown", "1", "en-US").subject == code_email("login", "1").subject

    def test_sms_text_locale_fallback(self):
        assert sms_text("verify", {"code": "42"}) == sms_text("verify", {"code": "42"}, "fr-FR")  # type: ignore[arg-type]
        assert sms_text("notice", {"title": "hi"}, "zh-CN") == "【SuperDL】hi"  # cjk-ok


class TestQuotaAndEndpoint:
    async def test_email_quota_is_separate_from_sms(self, sm, monkeypatch):
        from app.core import sms as sms_module
        from app.core.errors import AppError

        monkeypatch.setitem(email_module.EMAIL_PLATFORM_LIMITS, "verify", (1, 10))
        await email_module.ensure_email_platform_quota("verify")
        with pytest.raises(AppError):
            await email_module.ensure_email_platform_quota("verify")
        await sms_module.ensure_sms_platform_quota("verify")

    async def test_admin_test_email_uses_effective_provider(self, client: AsyncClient, sm):
        sent: list[tuple[str, str]] = []

        class _Recording:
            async def send(self, to, subject, text, html=None):
                sent.append((to, subject))

        email_module.set_email_channel(_Recording())
        headers = await admin_headers(sm, client)
        resp = await client.post(
            "/api/admin/v1/platform-config/test-email",
            json={"email": "Ops@Example.com"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"ok": True, "provider": "mock"}
        assert sent == [("ops@example.com", "SuperDL email channel test")]
        bad = await client.post(
            "/api/admin/v1/platform-config/test-email", json={"email": "nope"}, headers=headers
        )
        assert bad.status_code == 422

    async def test_admin_test_email_reports_channel_failure(self, client: AsyncClient, sm):
        class _Failing:
            async def send(self, to, subject, text, html=None):
                raise EmailError("smtp send failed: 535")

        email_module.set_email_channel(_Failing())
        headers = await admin_headers(sm, client)
        resp = await client.post(
            "/api/admin/v1/platform-config/test-email",
            json={"email": "ops@example.com"},
            headers=headers,
        )
        assert resp.status_code == 502
        assert resp.json()["message_key"] == "adminapi.emailTestFailed"
