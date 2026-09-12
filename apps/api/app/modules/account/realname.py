"""实名认证 provider seam(三要素核验:姓名 + 身份证号 + 手机号)。

开关 `real_name_enabled`(平台配置·安全策略);渠道只有阿里云 Mobile3MetaSimpleVerify,
凭据走平台配置中心。无 mock 渠道:关闭即 409,测试经 set_realname_provider 注入。
身份证号只存脱敏串(前 4 + 后 2),原文不留存、不进日志。
"""

from typing import Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.aliyun import rpc_call
from app.core.platform_config import get_effective_platform_config


class RealNameError(RuntimeError):
    """渠道故障。调用方转 AppError(REAL_NAME_CHANNEL_ERROR)。"""


class RealNameProvider(Protocol):
    async def verify(self, name: str, id_number: str, phone: str) -> bool:
        """三要素核验。渠道故障抛 RealNameError;核验不一致返回 False。"""
        ...


class AliyunRealNameProvider:
    """阿里云实人认证·手机号三要素核验简版(BizCode:1 一致 / 2 不一致 / 3 无记录)。"""

    ENDPOINT = "https://cloudauth.aliyuncs.com/"

    def __init__(
        self,
        access_key_id: str,
        access_key_secret: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._ak = access_key_id
        self._secret = access_key_secret
        self._transport = transport  # 测试注入 MockTransport

    def request_params(self, name: str, id_number: str, phone: str) -> dict[str, str]:
        """Mobile3MetaSimpleVerify 业务参数(公共参数与签名由 core/aliyun 补齐)。"""
        return {
            "Action": "Mobile3MetaSimpleVerify",
            "Version": "2019-03-07",
            "RegionId": "cn-hangzhou",
            "ParamType": "normal",
            "UserName": name,
            "IdentifyNum": id_number,
            "Mobile": phone,
        }

    async def verify(self, name: str, id_number: str, phone: str) -> bool:
        body = await rpc_call(
            self.ENDPOINT,
            self.request_params(name, id_number, phone),
            access_key_id=self._ak,
            access_key_secret=self._secret,
            transport=self._transport,
            error_cls=RealNameError,
        )
        if body.get("Code") != "200":
            raise RealNameError(f"realname rejected: {body.get('Code')} {body.get('Message')}")
        biz_code = (body.get("ResultObject") or {}).get("BizCode")
        if biz_code == "1":
            return True
        if biz_code in ("2", "3"):  # 不一致 / 无记录 = 未通过
            return False
        raise RealNameError(f"realname unexpected BizCode: {biz_code}")


_provider: RealNameProvider | None = None


def set_realname_provider(provider: RealNameProvider | None) -> None:
    """测试注入;传 None 恢复按配置构造。"""
    global _provider
    _provider = provider


async def get_realname_provider(session: AsyncSession) -> RealNameProvider:
    if _provider is not None:
        return _provider

    cfg = await get_effective_platform_config(session)
    if not (cfg["real_name_access_key_id"] and cfg["real_name_access_key_secret"]):
        raise RealNameError("阿里云实名认证凭据未配置(管理端·平台配置)")
    return AliyunRealNameProvider(
        cfg["real_name_access_key_id"], cfg["real_name_access_key_secret"]
    )


def mask_id_number(id_number: str) -> str:
    """脱敏:前 4 + 后 2,中间打星。"""
    return f"{id_number[:4]}{'*' * (len(id_number) - 6)}{id_number[-2:]}"


def mask_id_name(name: str) -> str:
    """姓名脱敏(管理端 readonly 角色):留姓掩名,单字全掩。"""
    if len(name) <= 1:
        return "*"
    return name[0] + "*" * (len(name) - 1)
