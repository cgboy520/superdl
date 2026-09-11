"""短信渠道 seam:阿里云签名、MockTransport 收发、验证码发送失败降级、通知短信尽力而为。"""

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.aliyun import rpc_signed_params
from app.core.sms import AliyunSmsChannel, SmsError, set_sms_channel
from tests.helpers import register


@pytest.fixture(autouse=True)
def _reset_channel():
    yield
    set_sms_channel(None)


class _FailingChannel:
    async def send(self, phone: str, template: str, params: dict[str, str]) -> None:
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

        ch = AliyunSmsChannel("ak", "sk", "SuperDL", transport=httpx.MockTransport(handler))
        await ch.send("13800000000", "SMS_123", {"code": "1234"})
        assert "Signature" in seen[0] and seen[0]["Action"] == "SendSms"
        with pytest.raises(SmsError, match="BUSINESS_LIMIT_CONTROL"):
            await ch.send("13800000000", "SMS_123", {"code": "1234"})


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
            assert row.used_at is not None  # 已作废,不可被消费


class TestNotifySmsBestEffort:
    async def test_notify_survives_channel_failure(self, client: AsyncClient, sm):
        """通知短信失败不影响站内信落库(尽力而为)。"""
        from app.modules.notify.models import Notification
        from app.modules.notify.service import send_low_balance_warning

        data = await register(client, "13800000092")
        set_sms_channel(_FailingChannel())
        async with sm() as session:
            await send_low_balance_warning(
                session, data["user"]["id"], est_hours=1.5, balance="3.20"
            )
        async with sm() as session:
            row = (
                await session.execute(
                    select(Notification).where(Notification.user_id == data["user"]["id"])
                )
            ).scalar_one()
            assert row.type == "balance_warn"


class TestLogRedaction:
    def test_mask_sensitive_processor(self):
        """日志打码:phone 前3后4,code/token/secret 整体打码,dict 值逐内层键同款。"""
        import logging

        from app.core.logging import _mask_sensitive_processor

        out = _mask_sensitive_processor(
            logging.getLogger("t"),
            "info",
            {
                "event": "mock_sms_sent",
                "phone": "13800000000",
                "access_token": "eyJabc.def",
                "client_secret": "s3cret",
                "params": {"code": "123456", "title": "余额预警"},
                "template": "SMS_123",
            },
        )
        assert out["phone"] == "138****0000"
        assert out["access_token"] == "******"
        assert out["client_secret"] == "******"
        assert out["params"] == {"code": "******", "title": "余额预警"}
        assert out["template"] == "SMS_123"


class TestPlatformQuota:
    """平台级短信配额(全局预算池)。"""

    async def test_sms_code_blocked_by_platform_quota(self, client: AsyncClient, sm, monkeypatch):
        """配额耗尽时验证码接口 429 RATE_LIMITED,且不落库无效验证码。"""
        from app.core import sms as sms_module
        from app.modules.account.models import SmsCode

        monkeypatch.setattr(sms_module, "SMS_PLATFORM_HOURLY_MAX", 1)
        await sms_module.ensure_sms_platform_quota()  # 占满配额
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
            async def send(self, phone: str, template: str, params: dict[str, str]) -> None:
                sent.append(phone)

        set_sms_channel(_CountingChannel())
        monkeypatch.setattr(sms_module, "SMS_PLATFORM_HOURLY_MAX", 1)
        await sms_module.ensure_sms_platform_quota()  # 占满配额
        task = OutboxTask(type="notify.sms", payload={"phone": "13800000094", "title": "余额预警"})
        async with sm() as session:
            await handle_notify_sms(session, task)  # 不抛 = 已消化
        assert sent == []
