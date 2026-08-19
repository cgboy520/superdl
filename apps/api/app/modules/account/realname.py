"""实名认证 provider seam(三要素核验:姓名 + 身份证号 + 手机号)。

mock:dev/test 即时通过(可注入失败);真实供应商(阿里云实人认证/腾讯云慧眼)
资质到位后按 Protocol 接入,业务流程零改动。PIPL 约束:身份证号不落明文,
只存脱敏展示串(前 4 + 后 2);核验结果即时返回,原文不留存。
"""

from typing import Protocol

from app.core.config import get_settings


class RealNameProvider(Protocol):
    async def verify(self, name: str, id_number: str, phone: str) -> bool:
        """三要素核验。渠道故障抛异常;核验不一致返回 False。"""
        ...


class MockRealNameProvider:
    """dev/test:默认全部通过;以 '0000' 结尾的身份证号模拟核验不一致。"""

    async def verify(self, name: str, id_number: str, phone: str) -> bool:
        return not id_number.endswith("0000")


_provider: RealNameProvider | None = None


def set_realname_provider(provider: RealNameProvider | None) -> None:
    """测试注入;None 恢复按配置构造。"""
    global _provider
    _provider = provider


def get_realname_provider() -> RealNameProvider:
    if _provider is not None:
        return _provider
    # 目前仅 mock;真实供应商接入时按 settings.real_name_provider 分支
    _ = get_settings()
    return MockRealNameProvider()


def mask_id_number(id_number: str) -> str:
    """脱敏:前 4 + 后 2,中间打星(仅此形态入库)。"""
    return f"{id_number[:4]}{'*' * (len(id_number) - 6)}{id_number[-2:]}"
