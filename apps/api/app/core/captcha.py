"""CAPTCHA channels: `aliyun` (Captcha 2.0 server-side verification) and `turnstile` (Cloudflare
siteverify). Callers own the `captcha_enabled` switch; a channel failure must block the action."""

from typing import Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.aliyun import rpc_call
from app.core.logging import get_logger
from app.core.platform_config import RuntimeConfig, get_runtime_config

logger = get_logger(__name__)


class CaptchaError(RuntimeError):
    """Channel-side failure; the caller refuses the guarded action."""


class CaptchaChannel(Protocol):
    async def verify(self, token: str, *, client_ip: str | None = None) -> bool:
        """Verify a one-time token. Raises CaptchaError on channel failure, returns False when the
        provider judges the token invalid."""
        ...


class AliyunCaptchaChannel:
    """Aliyun Captcha 2.0 server-side verification (captcha.cn-shanghai.aliyuncs.com, client
    `region=cn`)."""

    ENDPOINT = "https://captcha.cn-shanghai.aliyuncs.com/"

    def __init__(
        self,
        access_key_id: str,
        access_key_secret: str,
        scene_id: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._ak = access_key_id
        self._secret = access_key_secret
        self._scene_id = scene_id
        self._transport = transport

    def request_params(self, captcha_verify_param: str) -> dict[str, str]:
        """VerifyIntelligentCaptcha business parameters (common ones and the signature come from
        core/aliyun)."""
        return {
            "Action": "VerifyIntelligentCaptcha",
            "Version": "2023-03-05",
            "SceneId": self._scene_id,
            "CaptchaVerifyParam": captcha_verify_param,
        }

    async def verify(self, token: str, *, client_ip: str | None = None) -> bool:  # noqa: ARG002
        body = await rpc_call(
            self.ENDPOINT,
            self.request_params(token),
            access_key_id=self._ak,
            access_key_secret=self._secret,
            transport=self._transport,
            error_cls=CaptchaError,
        )
        if body.get("Code") not in ("Success", "OK"):
            raise CaptchaError(f"captcha rejected: {body.get('Code')} {body.get('Message')}")
        result = body.get("Result")
        if not isinstance(result, dict) or "VerifyResult" not in result:
            raise CaptchaError(f"captcha unexpected response: {body!r}")
        return bool(result["VerifyResult"])


class TurnstileCaptchaChannel:
    """Cloudflare Turnstile siteverify; the client IP is forwarded as `remoteip` when known.
    Token-specific rejections return False; secret / request / provider errors raise."""

    ENDPOINT = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
    TIMEOUT_SECONDS = 10.0
    CHANNEL_ERROR_CODES = frozenset(
        {"missing-input-secret", "invalid-input-secret", "bad-request", "internal-error"}
    )

    def __init__(self, secret: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._secret = secret
        self._transport = transport

    def request_form(self, token: str, client_ip: str | None) -> dict[str, str]:
        form = {"secret": self._secret, "response": token}
        if client_ip:
            form["remoteip"] = client_ip
        return form

    async def verify(self, token: str, *, client_ip: str | None = None) -> bool:
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self.TIMEOUT_SECONDS
            ) as client:
                resp = await client.post(self.ENDPOINT, data=self.request_form(token, client_ip))
            body = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CaptchaError(f"turnstile request failed: {exc}") from exc
        if not isinstance(body, dict) or not isinstance(body.get("success"), bool):
            raise CaptchaError(f"turnstile unexpected response: {body!r}")
        if not body["success"]:
            codes = body.get("error-codes")
            if isinstance(codes, list) and self.CHANNEL_ERROR_CODES.intersection(codes):
                raise CaptchaError(f"turnstile verification error: {codes}")
            logger.info("captcha_rejected", provider="turnstile", codes=codes)
        return body["success"]


_channel: CaptchaChannel | None = None


def set_captcha_channel(channel: CaptchaChannel | None) -> None:
    """Test seam; None restores construction from config."""
    global _channel
    _channel = channel


def build_captcha_channel(cfg: RuntimeConfig) -> CaptchaChannel:
    """Channel for the effective `captcha_provider`; incomplete credentials raise CaptchaError."""
    if cfg.captcha_provider == "turnstile":
        if not cfg.captcha_turnstile_secret_key:
            raise CaptchaError("Turnstile secret key not configured (captcha_turnstile_secret_key)")
        return TurnstileCaptchaChannel(cfg.captcha_turnstile_secret_key)
    if not (cfg.captcha_access_key_id and cfg.captcha_access_key_secret and cfg.captcha_scene_id):
        raise CaptchaError(
            "Aliyun captcha credentials / scene not configured "
            "(platform config center or SUPERDL_CAPTCHA_*)"
        )
    return AliyunCaptchaChannel(
        cfg.captcha_access_key_id, cfg.captcha_access_key_secret, cfg.captcha_scene_id
    )


async def get_captcha_channel(session: AsyncSession) -> CaptchaChannel:
    if _channel is not None:
        return _channel
    return build_captcha_channel(await get_runtime_config(session))
