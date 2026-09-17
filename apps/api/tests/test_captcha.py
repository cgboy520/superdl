"""CAPTCHA: Aliyun and Turnstile channel seams, provider factory, the captcha_enabled gate in
front of /auth/verification-code, and the public bootstrap config."""

import httpx
import pytest
from httpx import AsyncClient

from app.core.captcha import (
    AliyunCaptchaChannel,
    CaptchaError,
    TurnstileCaptchaChannel,
    build_captcha_channel,
    set_captcha_channel,
)
from app.core.platform_config import runtime_config_from_strings as rc
from tests.helpers import as_handle, set_platform_setting


@pytest.fixture(autouse=True)
def _reset_channel():
    yield
    set_captcha_channel(None)


class _FailingChannel:
    async def verify(self, token: str, *, client_ip: str | None = None) -> bool:
        raise CaptchaError("provider down")


class _RejectingChannel:
    async def verify(self, token: str, *, client_ip: str | None = None) -> bool:
        return False


class TestTurnstileChannel:
    async def test_success_false_and_remoteip(self):
        seen: list[dict[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = dict(pair.split("=", 1) for pair in request.content.decode().split("&"))
            seen.append(body)
            return httpx.Response(200, json={"success": body["response"] == "good"})

        ch = TurnstileCaptchaChannel("secret-1", transport=httpx.MockTransport(handler))
        assert await ch.verify("good", client_ip="203.0.113.9") is True
        assert seen[0] == {"secret": "secret-1", "response": "good", "remoteip": "203.0.113.9"}
        assert await ch.verify("bad") is False
        assert "remoteip" not in seen[1]

    async def test_transport_and_malformed_raise(self):
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        ch = TurnstileCaptchaChannel("s", transport=httpx.MockTransport(boom))
        with pytest.raises(CaptchaError, match="request failed"):
            await ch.verify("t")

        def malformed(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"hello": "world"})

        ch = TurnstileCaptchaChannel("s", transport=httpx.MockTransport(malformed))
        with pytest.raises(CaptchaError, match="unexpected"):
            await ch.verify("t")


class TestFactory:
    def test_provider_dispatch_and_credential_checks(self):
        with pytest.raises(CaptchaError, match="Turnstile"):
            build_captcha_channel(rc({"captcha_provider": "turnstile"}))
        ch = build_captcha_channel(
            rc({"captcha_provider": "turnstile", "captcha_turnstile_secret_key": "sec"})
        )
        assert isinstance(ch, TurnstileCaptchaChannel)
        with pytest.raises(CaptchaError, match="Aliyun"):
            build_captcha_channel(rc({"captcha_provider": "aliyun"}))
        ali = build_captcha_channel(
            rc(
                {
                    "captcha_provider": "aliyun",
                    "captcha_scene_id": "s",
                    "captcha_access_key_id": "LTAI5tTESTTESTTEST",
                    "captcha_access_key_secret": "k",
                }
            )
        )
        assert isinstance(ali, AliyunCaptchaChannel)


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
        assert seen[0]["SceneId"] == "scene-1"
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
            return httpx.Response(200, json={"Code": "Success"})

        ch2 = AliyunCaptchaChannel("ak", "sk", "s", transport=httpx.MockTransport(malformed))
        with pytest.raises(CaptchaError, match="unexpected"):
            await ch2.verify("t")


class TestSmsCodeGate:
    async def test_disabled_skips_verification(self, client: AsyncClient, sm):
        """开关关闭(默认):不带 token 直接发码,渠道不被调用。"""
        set_captcha_channel(_FailingChannel())
        resp = await client.post(
            "/api/v1/auth/verification-code",
            json={"handle": as_handle("13800000094"), "purpose": "register"},
        )
        assert resp.status_code == 204, resp.text

    async def test_enabled_requires_token(self, client: AsyncClient, sm):
        """开关开启:缺 token 即 400 CAPTCHA_REQUIRED。"""
        await set_platform_setting(sm, "captcha_enabled", "true")
        set_captcha_channel(_RejectingChannel())
        resp = await client.post(
            "/api/v1/auth/verification-code",
            json={"handle": as_handle("13800000095"), "purpose": "register"},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "CAPTCHA_REQUIRED"

    async def test_wrong_token_rejected(self, client: AsyncClient, sm):
        await set_platform_setting(sm, "captcha_enabled", "true")
        set_captcha_channel(_RejectingChannel())
        resp = await client.post(
            "/api/v1/auth/verification-code",
            json={
                "handle": as_handle("13800000095"),
                "purpose": "register",
                "captcha_token": "wrong",
            },
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "CAPTCHA_VERIFY_FAILED"

    async def test_channel_failure_is_fail_closed(self, client: AsyncClient, sm):
        """渠道故障 → 502。"""
        from sqlalchemy import select

        from app.modules.account.models import VerificationCode

        await set_platform_setting(sm, "captcha_enabled", "true")
        set_captcha_channel(_FailingChannel())
        resp = await client.post(
            "/api/v1/auth/verification-code",
            json={"handle": as_handle("13800000095"), "purpose": "register", "captcha_token": "t"},
        )
        assert resp.status_code == 502
        assert resp.json()["code"] == "CAPTCHA_CHANNEL_ERROR"
        async with sm() as session:
            row = (
                await session.execute(
                    select(VerificationCode).where(
                        VerificationCode.target == as_handle("13800000095")
                    )
                )
            ).scalar_one_or_none()
            assert row is None

    async def test_enabled_without_credentials_is_fail_closed(self, client: AsyncClient, sm):
        """开启但凭据未配 → 502。"""
        await set_platform_setting(sm, "captcha_enabled", "true")
        resp = await client.post(
            "/api/v1/auth/verification-code",
            json={"handle": as_handle("13800000096"), "purpose": "register", "captcha_token": "t"},
        )
        assert resp.status_code == 502
        assert resp.json()["code"] == "CAPTCHA_CHANNEL_ERROR"

    async def test_captcha_config_public(self, client: AsyncClient, sm):
        """前端初始化配置:免鉴权;开关即时跟随 DB 覆盖。"""
        resp = await client.get("/api/v1/auth/captcha-config")
        assert resp.status_code == 200
        assert resp.json() == {
            "enabled": False,
            "provider": "turnstile",
            "site_key": None,
            "scene_id": None,
            "prefix": None,
        }
        await set_platform_setting(sm, "captcha_enabled", "true")
        await set_platform_setting(sm, "captcha_turnstile_site_key", "0x4AAAAAAA_site")
        assert (await client.get("/api/v1/auth/captcha-config")).json() == {
            "enabled": True,
            "provider": "turnstile",
            "site_key": "0x4AAAAAAA_site",
            "scene_id": None,
            "prefix": None,
        }
        await set_platform_setting(sm, "captcha_provider", "aliyun")
        await set_platform_setting(sm, "captcha_scene_id", "scene-1")
        await set_platform_setting(sm, "captcha_prefix", "pfx")
        assert (await client.get("/api/v1/auth/captcha-config")).json() == {
            "enabled": True,
            "provider": "aliyun",
            "site_key": "0x4AAAAAAA_site",
            "scene_id": "scene-1",
            "prefix": "pfx",
        }
