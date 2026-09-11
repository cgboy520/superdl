"""人机校验:阿里云渠道 seam、captcha_enabled 开关下的 /auth/sms-code 前置闸、公开初始化配置。"""

import httpx
import pytest
from httpx import AsyncClient

from app.core.captcha import AliyunCaptchaChannel, CaptchaError, set_captcha_channel
from tests.helpers import set_platform_setting


@pytest.fixture(autouse=True)
def _reset_channel():
    yield
    set_captcha_channel(None)


class _FailingChannel:
    async def verify(self, captcha_verify_param: str) -> bool:
        raise CaptchaError("provider down")


class _RejectingChannel:
    async def verify(self, captcha_verify_param: str) -> bool:
        return False


class TestAliyunChannel:
    async def test_signature_and_result_parsing(self):
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = dict(pair.split("=", 1) for pair in request.content.decode().split("&"))
            seen.append(body)
            return httpx.Response(200, json={"Code": "Success", "Result": {"VerifyResult": True}})

        ch = AliyunCaptchaChannel("ak", "sk", "scene-1", transport=httpx.MockTransport(handler))
        assert await ch.verify("token-abc") is True
        assert seen[0]["Action"] == "VerifyIntelligentCaptcha"
        assert seen[0]["Version"] == "2023-03-05"
        assert seen[0]["SceneId"] == "scene-1"  # 服务端强制写场景,防前端篡改
        assert seen[0]["CaptchaVerifyParam"] == "token-abc"

    async def test_verify_result_false(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"Code": "Success", "Result": {"VerifyResult": False}})

        ch = AliyunCaptchaChannel("ak", "sk", "s", transport=httpx.MockTransport(handler))
        assert await ch.verify("bot-token") is False

    async def test_rejected_and_malformed_raise(self):
        def rejected(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"Code": "Throttling", "Message": "限流"})

        ch = AliyunCaptchaChannel("ak", "sk", "s", transport=httpx.MockTransport(rejected))
        with pytest.raises(CaptchaError, match="Throttling"):
            await ch.verify("t")

        def malformed(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"Code": "Success"})  # 缺 Result

        ch2 = AliyunCaptchaChannel("ak", "sk", "s", transport=httpx.MockTransport(malformed))
        with pytest.raises(CaptchaError, match="unexpected"):
            await ch2.verify("t")


class TestSmsCodeGate:
    async def test_disabled_skips_verification(self, client: AsyncClient, sm):
        """开关关闭(默认):不带 token 直接发码,渠道不被调用。"""
        set_captcha_channel(_FailingChannel())
        resp = await client.post(
            "/api/v1/auth/sms-code", json={"phone": "13800000094", "purpose": "register"}
        )
        assert resp.status_code == 204, resp.text

    async def test_enabled_requires_token(self, client: AsyncClient, sm):
        """开关开启:缺 token 即 400 CAPTCHA_REQUIRED。"""
        await set_platform_setting(sm, "captcha_enabled", "true")
        set_captcha_channel(_RejectingChannel())
        resp = await client.post(
            "/api/v1/auth/sms-code", json={"phone": "13800000095", "purpose": "register"}
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "CAPTCHA_REQUIRED"

    async def test_wrong_token_rejected(self, client: AsyncClient, sm):
        await set_platform_setting(sm, "captcha_enabled", "true")
        set_captcha_channel(_RejectingChannel())
        resp = await client.post(
            "/api/v1/auth/sms-code",
            json={"phone": "13800000095", "purpose": "register", "captcha_token": "wrong"},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "CAPTCHA_VERIFY_FAILED"

    async def test_channel_failure_is_fail_closed(self, client: AsyncClient, sm):
        """渠道故障 → 502。"""
        from sqlalchemy import select

        from app.modules.account.models import SmsCode

        await set_platform_setting(sm, "captcha_enabled", "true")
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

    async def test_enabled_without_credentials_is_fail_closed(self, client: AsyncClient, sm):
        """开启但凭据未配 → 502。"""
        await set_platform_setting(sm, "captcha_enabled", "true")
        resp = await client.post(
            "/api/v1/auth/sms-code",
            json={"phone": "13800000096", "purpose": "register", "captcha_token": "t"},
        )
        assert resp.status_code == 502
        assert resp.json()["code"] == "CAPTCHA_CHANNEL_ERROR"

    async def test_captcha_config_public(self, client: AsyncClient, sm):
        """前端初始化配置:免鉴权;开关即时跟随 DB 覆盖。"""
        resp = await client.get("/api/v1/auth/captcha-config")
        assert resp.status_code == 200
        assert resp.json() == {"enabled": False, "scene_id": None, "prefix": None}
        await set_platform_setting(sm, "captcha_enabled", "true")
        await set_platform_setting(sm, "captcha_scene_id", "scene-1")
        await set_platform_setting(sm, "captcha_prefix", "pfx")
        assert (await client.get("/api/v1/auth/captcha-config")).json() == {
            "enabled": True,
            "scene_id": "scene-1",
            "prefix": "pfx",
        }
