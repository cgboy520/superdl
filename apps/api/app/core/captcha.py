"""人机校验渠道 seam(阿里云验证码 2.0;与短信/实名同一 Protocol + 工厂模式)。

是否过这道闸由平台配置·安全策略的 `captcha_enabled` 决定(account.service 读开关);
开启后凭据与场景走平台配置中心(env SUPERDL_CAPTCHA_* 为默认值层)。没有 mock 渠道:
关闭即跳过,测试经 set_captcha_channel 注入假渠道。

安全语义:校验门 fail-closed —— 渠道故障(网络/签名/欠费)抛 CaptchaError,
调用方一律拒绝后续动作。
"""

from typing import Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.aliyun import rpc_call
from app.core.logging import get_logger

logger = get_logger(__name__)


class CaptchaError(RuntimeError):
    """渠道侧故障(网络/签名/欠费/异常响应)。fail-closed:调用方拒绝后续动作。"""


class CaptchaChannel(Protocol):
    async def verify(self, captcha_verify_param: str, client_ip: str | None) -> bool:
        """验签一次通过性校验(CaptchaVerifyParam 一次性,重复调用返 F008)。
        渠道故障抛 CaptchaError;人/机判定不通过返回 False。"""
        ...


class AliyunCaptchaChannel:
    """阿里云验证码 2.0 服务端验签。captcha.cn-shanghai.aliyuncs.com(中国内地),
    与客户端 region=cn 固定映射(映射错会验签失败,见阿里云服务端接入文档)。"""

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
        self._transport = transport  # 测试注入 MockTransport

    def request_params(self, captcha_verify_param: str) -> dict[str, str]:
        """VerifyIntelligentCaptcha 业务参数(公共参数与签名由 core/aliyun 补齐)。"""
        return {
            "Action": "VerifyIntelligentCaptcha",
            "Version": "2023-03-05",
            # 服务端强制写入场景:防前端被篡改到其它场景(阿里云官方建议)
            "SceneId": self._scene_id,
            "CaptchaVerifyParam": captcha_verify_param,
        }

    async def verify(self, captcha_verify_param: str, client_ip: str | None) -> bool:
        body = await rpc_call(
            self.ENDPOINT,
            self.request_params(captcha_verify_param),
            access_key_id=self._ak,
            access_key_secret=self._secret,
            transport=self._transport,
            error_cls=CaptchaError,
        )
        # Code 为请求级结果(Success/OK 兼容);人机判定在 Result.VerifyResult
        if body.get("Code") not in ("Success", "OK"):
            raise CaptchaError(f"captcha rejected: {body.get('Code')} {body.get('Message')}")
        result = body.get("Result")
        if not isinstance(result, dict) or "VerifyResult" not in result:
            raise CaptchaError(f"captcha unexpected response: {body!r}")
        return bool(result["VerifyResult"])


_channel: CaptchaChannel | None = None


def set_captcha_channel(channel: CaptchaChannel | None) -> None:
    """测试注入;传 None 恢复按配置构造。"""
    global _channel
    _channel = channel


async def get_captcha_channel(session: AsyncSession) -> CaptchaChannel:
    if _channel is not None:
        return _channel
    from app.core.platform_config import get_effective_platform_config

    cfg = await get_effective_platform_config(session)
    if not (
        cfg["captcha_access_key_id"]
        and cfg["captcha_access_key_secret"]
        and cfg["captcha_scene_id"]
    ):
        raise CaptchaError("阿里云验证码凭据/场景未配置(管理端·平台配置,或 SUPERDL_CAPTCHA_*)")
    return AliyunCaptchaChannel(
        cfg["captcha_access_key_id"], cfg["captcha_access_key_secret"], cfg["captcha_scene_id"]
    )
