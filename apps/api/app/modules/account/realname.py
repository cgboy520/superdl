"""实名认证 provider seam(三要素核验:姓名 + 身份证号 + 手机号)。

- mock:dev/test 即时通过(可注入失败);
- aliyun:实人认证·手机号三要素核验简版(Cloudauth 2019-03-07
  Mobile3MetaSimpleVerify),凭据走平台配置中心(env 为默认值层)。
PIPL 约束:身份证号不落明文,只存脱敏展示串(前 4 + 后 2);
核验结果即时返回,原文不留存、不进日志。
"""

from typing import Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.aliyun import rpc_call


class RealNameError(RuntimeError):
    """渠道故障(网络/签名/欠费)。调用方转 AppError(REAL_NAME_CHANNEL_ERROR)。"""


class RealNameProvider(Protocol):
    async def verify(self, name: str, id_number: str, phone: str) -> bool:
        """三要素核验。渠道故障抛 RealNameError;核验不一致返回 False。"""
        ...


class MockRealNameProvider:
    """dev/test:默认全部通过;以 '0000' 结尾的身份证号模拟核验不一致。"""

    async def verify(self, name: str, id_number: str, phone: str) -> bool:
        return not id_number.endswith("0000")


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
        if biz_code in ("2", "3"):  # 不一致 / 运营商无记录,均视为核验未通过
            return False
        raise RealNameError(f"realname unexpected BizCode: {biz_code}")


async def get_realname_provider(session: AsyncSession) -> RealNameProvider:
    from app.core.platform_config import get_effective_platform_config

    cfg = await get_effective_platform_config(session)
    if cfg["real_name_provider"] == "aliyun":
        if not (cfg["real_name_access_key_id"] and cfg["real_name_access_key_secret"]):
            raise RealNameError("阿里云实名认证凭据未配置(管理端·平台配置)")
        return AliyunRealNameProvider(
            cfg["real_name_access_key_id"], cfg["real_name_access_key_secret"]
        )
    return MockRealNameProvider()


def mask_id_number(id_number: str) -> str:
    """脱敏:前 4 + 后 2,中间打星(仅此形态入库)。"""
    return f"{id_number[:4]}{'*' * (len(id_number) - 6)}{id_number[-2:]}"


def mask_id_name(name: str) -> str:
    """姓名脱敏(管理端 readonly 角色):留姓掩名,单字全掩。"""
    if len(name) <= 1:
        return "*"
    return name[0] + "*" * (len(name) - 1)


def mask_company_name(name: str) -> str:
    """企业名脱敏(管理端 readonly 角色):留前 2 + 后 2;短名退化为只留首字。"""
    if len(name) <= 1:
        return "*"
    if len(name) <= 4:
        return name[0] + "*" * (len(name) - 1)
    return f"{name[:2]}{'*' * (len(name) - 4)}{name[-2:]}"
