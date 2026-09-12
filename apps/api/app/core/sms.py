"""短信渠道(Protocol + 工厂):mock 落结构化日志;aliyun dysmsapi SendSms,凭据走平台配置中心。"""

import json
from typing import Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.aliyun import rpc_call
from app.core.logging import get_logger
from app.core.platform_config import get_effective_platform_config
from app.core.ratelimit import check_rate_limit

logger = get_logger(__name__)

# 平台级短信预算闸门(验证码与通知共享;计数落 PG):小时窗 + 日窗
SMS_PLATFORM_HOURLY_MAX = 1000
SMS_PLATFORM_DAILY_MAX = 5000


async def ensure_sms_platform_quota() -> None:
    """平台级短信闸门,超限抛 RATE_LIMITED(429);每个 channel.send 之前必须先过,计数含失败尝试。"""
    await check_rate_limit(
        "sms-platform:hourly", max_attempts=SMS_PLATFORM_HOURLY_MAX, window_seconds=3600.0
    )
    await check_rate_limit(
        "sms-platform:daily", max_attempts=SMS_PLATFORM_DAILY_MAX, window_seconds=86400.0
    )


class SmsError(RuntimeError):
    """渠道侧发送失败。"""


class SmsChannel(Protocol):
    async def send(self, phone: str, template: str, params: dict[str, str]) -> None:
        """发送一条模板短信。template 为渠道侧模板码。失败抛 SmsError。"""
        ...


class MockSmsChannel:
    async def send(self, phone: str, template: str, params: dict[str, str]) -> None:
        # 手机号与 params.code 由 logging._mask_sensitive_processor 打码
        logger.info("mock_sms_sent", phone=phone, template=template, params=params)


class AliyunSmsChannel:
    """阿里云 dysmsapi SendSms(RPC HMAC-SHA1 签名)。"""

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

    def request_params(self, phone: str, template: str, params: dict[str, str]) -> dict[str, str]:
        """SendSms 业务参数(公共参数与签名由 core/aliyun 补齐)。"""
        return {
            "Action": "SendSms",
            "PhoneNumbers": phone,
            "RegionId": "cn-hangzhou",
            "SignName": self._sign_name,
            "TemplateCode": template,
            "TemplateParam": json.dumps(params, ensure_ascii=False, separators=(",", ":")),
            "Version": "2017-05-25",
        }

    async def send(self, phone: str, template: str, params: dict[str, str]) -> None:
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


_channel: SmsChannel | None = None


def set_sms_channel(channel: SmsChannel | None) -> None:
    """测试注入;传 None 恢复按配置构造。"""
    global _channel
    _channel = channel


async def get_sms_channel(session: AsyncSession) -> SmsChannel:
    if _channel is not None:
        return _channel

    cfg = await get_effective_platform_config(session)
    if cfg["sms_provider"] == "mock":
        return MockSmsChannel()
    if not (cfg["sms_access_key_id"] and cfg["sms_access_key_secret"] and cfg["sms_sign_name"]):
        raise SmsError("阿里云短信凭据未配置(管理端·平台配置,或 SUPERDL_SMS_*)")
    return AliyunSmsChannel(
        cfg["sms_access_key_id"], cfg["sms_access_key_secret"], cfg["sms_sign_name"]
    )
