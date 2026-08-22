"""短信渠道抽象(账号验证码与通知短信共用)。

镜像 payment_channels 的 Protocol + 工厂模式:
- mock:落结构化日志(dev/test 默认);
- aliyun:dysmsapi SendSms(RPC 签名 V1),凭据与模板码走平台配置中心
  (env SUPERDL_SMS_* 为默认值层,DB 覆盖免重启生效)。
prod 下配置完整性由 Settings 校验把关。
"""

import json
from typing import Protocol
from uuid import uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.aliyun import rpc_signed_params
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
        # 验证码明文不落日志(日志可能被集中采集):code 打码;mock 验证码本就是固定值,不影响联调
        safe_params = {k: ("******" if k == "code" else v) for k, v in params.items()}
        logger.info("mock_sms_sent", phone=phone, template=template, params=safe_params)


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
        return rpc_signed_params(
            {
                "Action": "SendSms",
                "PhoneNumbers": phone,
                "RegionId": "cn-hangzhou",
                "SignName": self._sign_name,
                "TemplateCode": template,
                "TemplateParam": json.dumps(params, ensure_ascii=False, separators=(",", ":")),
                "Version": "2017-05-25",
            },
            access_key_id=self._ak,
            access_key_secret=self._secret,
            nonce=nonce,
            timestamp=timestamp,
        )

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


async def get_sms_channel(session: AsyncSession) -> SmsChannel:
    if _channel is not None:
        return _channel
    from app.core.platform_config import get_effective_platform_config

    cfg = await get_effective_platform_config(session)
    if cfg["sms_provider"] == "mock":
        return MockSmsChannel()
    if not (cfg["sms_access_key_id"] and cfg["sms_access_key_secret"] and cfg["sms_sign_name"]):
        raise SmsError("阿里云短信凭据未配置(管理端·平台配置,或 SUPERDL_SMS_*)")
    return AliyunSmsChannel(
        cfg["sms_access_key_id"], cfg["sms_access_key_secret"], cfg["sms_sign_name"]
    )
