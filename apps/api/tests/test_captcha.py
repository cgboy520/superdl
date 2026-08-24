"""人机校验(P1-17):mock/aliyun 渠道 seam、/auth/sms-code 前置闸、公开初始化配置。"""

import httpx
import pytest
from httpx import AsyncClient

from app.core.captcha import (
    MOCK_CAPTCHA_PASS_TOKEN,
    AliyunCaptchaChannel,
    CaptchaError,
    MockCaptchaChannel,
    set_captcha_channel,
)


@pytest.fixture(autouse=True)
def _reset_channel():
    yield
    set_captcha_channel(None)


class _FailingChannel:
    async def verify(self, captcha_verify_param: str, client_ip: str | None) -> bool:
        raise CaptchaError("provider down")


class TestMockChannel:
    async def test_pass_token_accepted(self):
        assert await MockCaptchaChannel().verify(MOCK_CAPTCHA_PASS_TOKEN, None) is True
        assert await MockCaptchaChannel().verify("anything-else", None) is False


class TestAliyunChannel:
    async def test_signature_and_result_parsing(self):
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = dict(pair.split("=", 1) for pair in request.content.decode().split("&"))
            seen.append(body)
            return httpx.Response(200, json={"Code": "Success", "Result": {"VerifyResult": True}})

        ch = AliyunCaptchaChannel("ak", "sk", "scene-1", transport=httpx.MockTransport(handler))
        assert await ch.verify("token-abc", None) is True
        assert seen[0]["Action"] == "VerifyIntelligentCaptcha"
        assert seen[0]["Version"] == "2023-03-05"
        assert seen[0]["SceneId"] == "scene-1"  # 服务端强制写场景,防前端篡改
        assert seen[0]["CaptchaVerifyParam"] == "token-abc"

    async def test_verify_result_false(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"Code": "Success", "Result": {"VerifyResult": False}})

        ch = AliyunCaptchaChannel("ak", "sk", "s", transport=httpx.MockTransport(handler))
        assert await ch.verify("bot-token", None) is False

    async def test_rejected_and_malformed_raise(self):
        def rejected(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"Code": "Throttling", "Message": "限流"})

        ch = AliyunCaptchaChannel("ak", "sk", "s", transport=httpx.MockTransport(rejected))
        with pytest.raises(CaptchaError, match="Throttling"):
            await ch.verify("t", None)

        def malformed(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"Code": "Success"})  # 缺 Result

        ch2 = AliyunCaptchaChannel("ak", "sk", "s", transport=httpx.MockTransport(malformed))
        with pytest.raises(CaptchaError, match="unexpected"):
            await ch2.verify("t", None)


class TestSmsCodeGate:
    async def test_missing_token_rejected(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/auth/sms-code", json={"phone": "13800000095", "purpose": "register"}
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "CAPTCHA_REQUIRED"

    async def test_wrong_token_rejected(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/auth/sms-code",
            json={"phone": "13800000095", "purpose": "register", "captcha_token": "wrong"},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "CAPTCHA_VERIFY_FAILED"

    async def test_channel_failure_is_fail_closed(self, client: AsyncClient, sm):
        """渠道故障 → 502(fail-closed):宁停发码服务,不向轰炸敞开。"""
        from sqlalchemy import select

        from app.modules.account.models import SmsCode

        set_captcha_channel(_FailingChannel())
        resp = await client.post(
            "/api/v1/auth/sms-code",
            json={"phone": "13800000095", "purpose": "register", "captcha_token": "t"},
        )
        assert resp.status_code == 502
        assert resp.json()["code"] == "CAPTCHA_CHANNEL_ERROR"
        async with sm() as session:
            row = (
                await session.execute(select(SmsCode).where(SmsCode.phone == "13800000095"))
            ).scalar_one_or_none()
            assert row is None  # 未落库:闸门在写库之前

    async def test_captcha_config_public(self, client: AsyncClient):
        """前端初始化配置:免鉴权;mock 环境 scene/prefix 为空(前端据此直传放行串)。"""
        resp = await client.get("/api/v1/auth/captcha-config")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"provider": "mock", "scene_id": None, "prefix": None}
