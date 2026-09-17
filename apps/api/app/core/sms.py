"""SMS channels (Protocol + factory): `mock` logs, `aliyun` sends dysmsapi templates by kind,
`twilio` sends Messages API bodies rendered from `core/verification/templates`. Credentials come
from the platform config center."""

import json
from typing import Literal, Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.aliyun import rpc_call
from app.core.locale import DEFAULT_LOCALE, Locale
from app.core.logging import get_logger
from app.core.platform_config import RuntimeConfig, get_runtime_config
from app.core.ratelimit import check_rate_limit
from app.core.verification.templates import sms_text

logger = get_logger(__name__)

SmsKind = Literal["verify", "notice"]
SmsQuotaKind = Literal["verify", "notify"]

SMS_PLATFORM_LIMITS: dict[SmsQuotaKind, tuple[int, int]] = {
    "verify": (1000, 5000),
    "notify": (500, 2000),
}


async def ensure_sms_platform_quota(kind: SmsQuotaKind = "verify") -> None:
    """Platform-wide SMS budget per kind (hourly / daily); `verify` = sign-up / sign-in / reset,
    `notify` = platform notifications. Raises RATE_LIMITED (429). Every `channel.send` must pass
    it first; failed attempts count too."""
    hourly, daily = SMS_PLATFORM_LIMITS[kind]
    await check_rate_limit(
        f"sms-platform:{kind}:hourly", max_attempts=hourly, window_seconds=3600.0
    )
    await check_rate_limit(f"sms-platform:{kind}:daily", max_attempts=daily, window_seconds=86400.0)


class SmsError(RuntimeError):
    """The carrier refused or the request failed."""


class SmsChannel(Protocol):
    async def send(
        self,
        phone: str,
        kind: SmsKind,
        params: dict[str, str],
        *,
        locale: Locale = DEFAULT_LOCALE,
    ) -> None:
        """Send one `verify` (params: code) or `notice` (params: title) SMS. Raises SmsError."""
        ...


class MockSmsChannel:
    async def send(
        self,
        phone: str,
        kind: SmsKind,
        params: dict[str, str],
        *,
        locale: Locale = DEFAULT_LOCALE,
    ) -> None:
        logger.info("mock_sms_sent", phone=phone, kind=kind, params=params, locale=locale)


class AliyunSmsChannel:
    """Aliyun dysmsapi SendSms (RPC HMAC-SHA1 signature); one registered template code per kind."""

    ENDPOINT = "https://dysmsapi.aliyuncs.com/"

    def __init__(
        self,
        access_key_id: str,
        access_key_secret: str,
        sign_name: str,
        *,
        templates: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._ak = access_key_id
        self._secret = access_key_secret
        self._sign_name = sign_name
        self._templates = {k: v for k, v in (templates or {}).items() if v}
        self._transport = transport

    def request_params(self, phone: str, template: str, params: dict[str, str]) -> dict[str, str]:
        """SendSms business parameters (common parameters and signature come from core/aliyun)."""
        return {
            "Action": "SendSms",
            "PhoneNumbers": phone,
            "RegionId": "cn-hangzhou",
            "SignName": self._sign_name,
            "TemplateCode": template,
            "TemplateParam": json.dumps(params, ensure_ascii=False, separators=(",", ":")),
            "Version": "2017-05-25",
        }

    async def send(
        self,
        phone: str,
        kind: SmsKind,
        params: dict[str, str],
        *,
        locale: Locale = DEFAULT_LOCALE,  # noqa: ARG002
    ) -> None:
        template = self._templates.get(kind)
        if not template:
            raise SmsError(
                f"no Aliyun template code configured for {kind} SMS "
                "(sms_template_verify / sms_template_notice)"
            )
        body = await rpc_call(
            self.ENDPOINT,
            self.request_params(phone, template, params),
            access_key_id=self._ak,
            access_key_secret=self._secret,
            transport=self._transport,
            error_cls=SmsError,
        )
        if body.get("Code") != "OK":
            raise SmsError(f"sms rejected: {body.get('Code')} {body.get('Message')}")


class TwilioSmsChannel:
    """Twilio Messages API (HTTP basic auth); the sender is a Twilio number or a Messaging
    Service SID (`MG…`); bodies come from the verification templates."""

    ENDPOINT = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    TIMEOUT_SECONDS = 10.0

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        sender: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._sid = account_sid
        self._token = auth_token
        self._sender = sender
        self._transport = transport

    def request_form(
        self, phone: str, kind: SmsKind, params: dict[str, str], locale: Locale
    ) -> dict[str, str]:
        form = {"To": phone, "Body": sms_text(kind, params, locale)}
        form["MessagingServiceSid" if self._sender.startswith("MG") else "From"] = self._sender
        return form

    async def send(
        self,
        phone: str,
        kind: SmsKind,
        params: dict[str, str],
        *,
        locale: Locale = DEFAULT_LOCALE,
    ) -> None:
        url = self.ENDPOINT.format(sid=self._sid)
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=self.TIMEOUT_SECONDS,
                auth=(self._sid, self._token),
            ) as client:
                resp = await client.post(url, data=self.request_form(phone, kind, params, locale))
        except httpx.HTTPError as exc:
            raise SmsError(f"twilio request failed: {exc}") from exc
        if resp.status_code >= 300:
            try:
                detail = resp.json()
                message = f"{detail.get('code')} {detail.get('message')}"
            except ValueError:
                message = resp.text[:200]
            raise SmsError(f"twilio rejected: {resp.status_code} {message}")


_channel: SmsChannel | None = None


def set_sms_channel(channel: SmsChannel | None) -> None:
    """Test seam; None restores construction from config."""
    global _channel
    _channel = channel


def build_sms_channel(cfg: RuntimeConfig) -> SmsChannel:
    """Channel for the effective `sms_provider`; incomplete credentials raise SmsError."""
    if cfg.sms_provider == "mock":
        return MockSmsChannel()
    if cfg.sms_provider == "twilio":
        if not (cfg.sms_twilio_account_sid and cfg.sms_twilio_auth_token and cfg.sms_twilio_from):
            raise SmsError(
                "Twilio SMS credentials incomplete "
                "(sms_twilio_account_sid / sms_twilio_auth_token / sms_twilio_from)"
            )
        return TwilioSmsChannel(
            cfg.sms_twilio_account_sid, cfg.sms_twilio_auth_token, cfg.sms_twilio_from
        )
    if not (cfg.sms_access_key_id and cfg.sms_access_key_secret and cfg.sms_sign_name):
        raise SmsError(
            "Aliyun SMS credentials incomplete (platform config center or SUPERDL_SMS_*)"
        )
    return AliyunSmsChannel(
        cfg.sms_access_key_id,
        cfg.sms_access_key_secret,
        cfg.sms_sign_name,
        templates={"verify": cfg.sms_template_verify, "notice": cfg.sms_template_notice},
    )


async def get_sms_channel(session: AsyncSession) -> SmsChannel:
    if _channel is not None:
        return _channel
    return build_sms_channel(await get_runtime_config(session))
