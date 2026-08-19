"""短信渠道抽象(账号验证码与通知短信共用,故放 core 而非业务模块)。

镜像 payment_channels 的 Protocol + 工厂模式:
- mock:落结构化日志(dev/test 默认);
- aliyun:dysmsapi SendSms(RPC 签名 V1),凭据与模板码经 SUPERDL_SMS_* 注入。
签名/模板报备是人工事项 #6;prod 下配置完整性由 Settings 校验把关。
"""

import base64
import hashlib
import hmac
import json
import urllib.parse
from typing import Protocol
from uuid import uuid4

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.timeutil import now_utc

logger = get_logger(__name__)


class SmsError(RuntimeError):
    """渠道侧发送失败(网络/签名/模板/欠费)。调用方决定降级还是上抛。"""


class SmsChannel(Protocol):
    async def send(self, phone: str, template: str, params: dict[str, str]) -> None:
        """发送一条模板短信。template 为渠道侧模板码。失败抛 SmsError。"""
        ...


class MockSmsChannel:
    async def send(self, phone: str, template: str, params: dict[str, str]) -> None:
        logger.info("mock_sms_sent", phone=phone, template=template, params=params)


def _percent_encode(value: str) -> str:
    # 阿里云 RPC 签名要求 RFC3986:仅 A-Za-z0-9-_.~ 不编码,空格 %20,* %2A
    return urllib.parse.quote(value, safe="")


class AliyunSmsChannel:
    """阿里云 dysmsapi SendSms。只实现发送所需最小面,签名算法为 RPC HMAC-SHA1。"""

    ENDPOINT = "https://dysmsapi.aliyuncs.com/"

    def __init__(
        self,
        access_key_id: str,
        access_key_secret: str,
        sign_name: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._ak = access_key_id
        self._secret = access_key_secret
        self._sign_name = sign_name
        self._transport = transport  # 测试注入 MockTransport

    def signed_params(
        self,
        phone: str,
        template: str,
        params: dict[str, str],
        *,
        nonce: str,
        timestamp: str,
    ) -> dict[str, str]:
        query = {
            "AccessKeyId": self._ak,
            "Action": "SendSms",
            "Format": "JSON",
            "PhoneNumbers": phone,
            "RegionId": "cn-hangzhou",
            "SignName": self._sign_name,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureNonce": nonce,
            "SignatureVersion": "1.0",
            "TemplateCode": template,
            "TemplateParam": json.dumps(params, ensure_ascii=False, separators=(",", ":")),
            "Timestamp": timestamp,
            "Version": "2017-05-25",
        }
        canonical = "&".join(
            f"{_percent_encode(k)}={_percent_encode(v)}" for k, v in sorted(query.items())
        )
        to_sign = f"POST&{_percent_encode('/')}&{_percent_encode(canonical)}"
        digest = hmac.new((self._secret + "&").encode(), to_sign.encode(), hashlib.sha1).digest()
        return {**query, "Signature": base64.b64encode(digest).decode()}

    async def send(self, phone: str, template: str, params: dict[str, str]) -> None:
        signed = self.signed_params(
            phone,
            template,
            params,
            nonce=uuid4().hex,
            timestamp=now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        try:
            async with httpx.AsyncClient(timeout=10, transport=self._transport) as client:
                resp = await client.post(self.ENDPOINT, data=signed)
            body = resp.json()
        except Exception as exc:
            raise SmsError(f"sms request failed: {exc}") from exc
        if body.get("Code") != "OK":
            raise SmsError(f"sms rejected: {body.get('Code')} {body.get('Message')}")


_channel: SmsChannel | None = None


def set_sms_channel(channel: SmsChannel | None) -> None:
    """测试注入;传 None 恢复按配置构造。"""
    global _channel
    _channel = channel


def get_sms_channel() -> SmsChannel:
    if _channel is not None:
        return _channel
    settings = get_settings()
    if settings.sms_provider == "mock":
        return MockSmsChannel()
    if not (
        settings.sms_access_key_id and settings.sms_access_key_secret and settings.sms_sign_name
    ):
        raise SmsError("阿里云短信凭据未配置(SUPERDL_SMS_ACCESS_KEY_ID/SECRET/SIGN_NAME)")
    return AliyunSmsChannel(
        settings.sms_access_key_id, settings.sms_access_key_secret, settings.sms_sign_name
    )
