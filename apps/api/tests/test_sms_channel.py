"""短信渠道 seam:阿里云签名、MockTransport 收发、验证码发送失败降级、平台配额。"""

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.aliyun import rpc_signed_params
from app.core.sms import AliyunSmsChannel, SmsError, TwilioSmsChannel, set_sms_channel


@pytest.fixture(autouse=True)
def _reset_channel():
    yield
    set_sms_channel(None)


class _FailingChannel:
    async def send(
        self, phone: str, kind: str, params: dict[str, str], *, locale: str = "en-US"
    ) -> None:
        raise SmsError("provider down")


class TestAliyunSignature:
    def test_signature_snapshot(self):
        """RPC V1 签名锚点(排序 / RFC3986 编码 / HMAC-SHA1)。"""
        ch = AliyunSmsChannel("testid", "testsecret", "SuperDL")
        p = rpc_signed_params(
            ch.request_params("13800000000", "SMS_123", {"code": "654321"}),
            access_key_id="testid",
            access_key_secret="testsecret",
            nonce="fixed-nonce",
            timestamp="2026-08-19T12:00:00Z",
        )
        assert p["Signature"] == "NWkyltdUd0U90o8ecQHBMY1f67U="
        assert p["TemplateParam"] == '{"code":"654321"}'
        assert p["SignName"] == "SuperDL"

    async def test_send_ok_and_rejected(self):
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = dict(pair.split("=", 1) for pair in request.content.decode().split("&"))
            seen.append(body)
            if len(seen) == 1:
                return httpx.Response(200, json={"Code": "OK"})
            return httpx.Response(
                200, json={"Code": "isv.BUSINESS_LIMIT_CONTROL", "Message": "限流"}
            )

        ch = AliyunSmsChannel(
            "ak",
            "sk",
            "SuperDL",
            templates={"verify": "SMS_123", "notice": ""},
            transport=httpx.MockTransport(handler),
        )
        await ch.send("13800000000", "verify", {"code": "1234"})
        assert "Signature" in seen[0] and seen[0]["Action"] == "SendSms"
        assert seen[0]["TemplateCode"] == "SMS_123"
        with pytest.raises(SmsError, match="BUSINESS_LIMIT_CONTROL"):
            await ch.send("13800000000", "verify", {"code": "1234"})

    async def test_missing_template_code_for_kind_is_a_channel_error(self):
        """kind → template mapping lives in the channel; an unconfigured kind never hits the API."""
        ch = AliyunSmsChannel("ak", "sk", "SuperDL", templates={"verify": "SMS_123"})
        with pytest.raises(SmsError, match="notice"):
            await ch.send("13800000000", "notice", {"title": "x"})


class TestTwilio:
    def test_request_form_uses_from_or_messaging_service(self):
        number = TwilioSmsChannel("AC" + "0" * 32, "tok", "+14155550123")
        form = number.request_form("+8613800001111", "verify", {"code": "654321"}, "en-US")
        assert form["To"] == "+8613800001111" and form["From"] == "+14155550123"
        assert "654321" in form["Body"] and "MessagingServiceSid" not in form
        service = TwilioSmsChannel("AC" + "0" * 32, "tok", "MG" + "a" * 32)
        form = service.request_form("+8613800001111", "notice", {"title": "Balance low"}, "zh-CN")
        assert form["MessagingServiceSid"] == "MG" + "a" * 32 and "From" not in form
        assert form["Body"] == "【SuperDL】Balance low"

    async def test_send_ok_and_rejected(self):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if len(seen) == 1:
                return httpx.Response(201, json={"sid": "SM1", "status": "queued"})
            return httpx.Response(400, json={"code": 21211, "message": "Invalid 'To' number"})

        ch = TwilioSmsChannel(
            "AC" + "1" * 32, "secret-token", "+14155550123", transport=httpx.MockTransport(handler)
        )
        await ch.send("+8613800001111", "verify", {"code": "1234"})
        assert seen[0].url.path == "/2010-04-01/Accounts/AC" + "1" * 32 + "/Messages.json"
        assert seen[0].headers["authorization"].startswith("Basic ")
        assert b"To=%2B8613800001111" in seen[0].content
        with pytest.raises(SmsError, match="21211"):
            await ch.send("+8613800001111", "verify", {"code": "1234"})


class TestFactory:
    def test_provider_dispatch_and_credential_checks(self):
        from app.core.platform_config import runtime_config_from_strings as rc
        from app.core.sms import MockSmsChannel, build_sms_channel

        assert isinstance(build_sms_channel(rc({"sms_provider": "mock"})), MockSmsChannel)
        with pytest.raises(SmsError, match="Twilio"):
            build_sms_channel(rc({"sms_provider": "twilio"}))
        twilio = build_sms_channel(
            rc(
                {
                    "sms_provider": "twilio",
                    "sms_twilio_account_sid": "AC" + "2" * 32,
                    "sms_twilio_auth_token": "t",
                    "sms_twilio_from": "+14155550123",
                }
            )
        )
        assert isinstance(twilio, TwilioSmsChannel)
        with pytest.raises(SmsError, match="Aliyun"):
            build_sms_channel(rc({"sms_provider": "aliyun"}))


class TestVerifyCodeSendFailure:
    async def test_channel_failure_invalidates_code(self, client: AsyncClient, sm):
        """渠道失败 → 502 SMS_SEND_FAILED,且刚落库的验证码被作废。"""
        from app.modules.account.models import SmsCode

        set_sms_channel(_FailingChannel())
        resp = await client.post(
            "/api/v1/auth/sms-code",
            json={"phone": "13800000090", "purpose": "register"},
        )
        assert resp.status_code == 502
        assert resp.json()["code"] == "SMS_SEND_FAILED"
        async with sm() as session:
            row = (
                await session.execute(select(SmsCode).where(SmsCode.phone == "13800000090"))
            ).scalar_one()
            assert row.used_at is not None


class TestPlatformQuota:
    """平台级短信配额(全局预算池)。"""

    async def test_sms_code_blocked_by_platform_quota(self, client: AsyncClient, sm, monkeypatch):
        """配额耗尽时验证码接口 429 RATE_LIMITED,且不落库无效验证码。"""
        from app.core import sms as sms_module
        from app.modules.account.models import SmsCode

        monkeypatch.setitem(sms_module.SMS_PLATFORM_LIMITS, "verify", (1, 5000))
        await sms_module.ensure_sms_platform_quota("verify")
        resp = await client.post(
            "/api/v1/auth/sms-code",
            json={"phone": "13800000093", "purpose": "register"},
        )
        assert resp.status_code == 429
        assert resp.json()["code"] == "RATE_LIMITED"
        async with sm() as session:
            row = (
                await session.execute(select(SmsCode).where(SmsCode.phone == "13800000093"))
            ).scalar_one_or_none()
            assert row is None

    async def test_notify_sms_digested_when_quota_exhausted(self, sm, monkeypatch):
        """通知短信遇配额耗尽:消化不重试、不触达渠道。"""
        from app.core import sms as sms_module
        from app.core.outbox import OutboxTask
        from app.modules.notify.service import handle_notify_sms

        sent: list[str] = []

        class _CountingChannel:
            async def send(
                self, phone: str, kind: str, params: dict[str, str], *, locale: str = "en-US"
            ) -> None:
                sent.append(phone)

        set_sms_channel(_CountingChannel())
        monkeypatch.setitem(sms_module.SMS_PLATFORM_LIMITS, "notify", (1, 2000))
        await sms_module.ensure_sms_platform_quota("notify")
        task = OutboxTask(type="notify.sms", payload={"phone": "13800000094", "title": "余额预警"})
        async with sm() as session:
            await handle_notify_sms(session, task)
        assert sent == []
