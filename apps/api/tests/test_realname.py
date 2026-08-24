"""实名认证 seam:核验通过/不一致/重复提交/脱敏入库;充值前强制开关。"""

from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import get_settings
from app.modules.account.models import User
from app.modules.account.realname import mask_phone
from tests.test_account_auth import register


def test_mask_phone_helper():
    """手机号脱敏单一定义点:前 3 后 4;短串退化为全掩(防前后段重叠泄露)。"""
    assert mask_phone("13812345678") == "138****5678"
    assert mask_phone("12345") == "***"
    assert mask_phone("") == "***"


async def _headers(client: AsyncClient, phone: str) -> tuple[dict, int]:
    data = await register(client, phone)
    return {"Authorization": f"Bearer {data['access_token']}"}, data["user"]["id"]


class TestRealName:
    async def test_verify_success_masks_id_number(self, client: AsyncClient, sm):
        headers, user_id = await _headers(client, "13800000160")
        resp = await client.post(
            "/api/v1/me/real-name",
            json={"name": "张三", "id_number": "110101199001011234"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["verification_status"] == "verified"
        async with sm() as session:
            user = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
            assert user.id_name == "张三"
            assert user.id_number is not None
            assert user.id_number.startswith("1101")
            assert "*" in user.id_number
            assert "199001011234" not in user.id_number  # 不落明文

        # 重复提交被拒
        resp = await client.post(
            "/api/v1/me/real-name",
            json={"name": "张三", "id_number": "110101199001011234"},
            headers=headers,
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "CONFLICT"

    async def test_mismatch_rejected(self, client: AsyncClient):
        headers, _ = await _headers(client, "13800000161")
        resp = await client.post(
            "/api/v1/me/real-name",
            json={"name": "李四", "id_number": "110101199001010000"},  # mock:0000 结尾不一致
            headers=headers,
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "REAL_NAME_MISMATCH"

    async def test_provider_misconfigured_is_502_not_500(self, client: AsyncClient, sm):
        """real_name_provider=aliyun 但凭据未配置:取 provider 即抛 RealNameError,
        必须走设计好的 502 渠道故障(与 verify 失败同径),不能漏成 500。"""
        from app.core.platform_config import PlatformSetting

        async with sm() as session:
            session.add(PlatformSetting(key="real_name_provider", value="aliyun"))
            await session.commit()
        headers, _ = await _headers(client, "13800000164")
        resp = await client.post(
            "/api/v1/me/real-name",
            json={"name": "张三", "id_number": "110101199001011234"},
            headers=headers,
        )
        assert resp.status_code == 502
        assert resp.json()["code"] == "REAL_NAME_CHANNEL_ERROR"

    async def test_recharge_gate_when_required(self, client: AsyncClient):
        settings = get_settings()
        settings.real_name_required_for_recharge = True
        try:
            headers, _ = await _headers(client, "13800000162")
            resp = await client.post(
                "/api/v1/wallet/recharges",
                json={"amount": "50.00", "channel": "mock"},
                headers=headers,
            )
            assert resp.status_code == 403
            assert resp.json()["code"] == "REAL_NAME_REQUIRED"

            # 完成实名后放行
            await client.post(
                "/api/v1/me/real-name",
                json={"name": "王五", "id_number": "110101199001011111"},
                headers=headers,
            )
            resp = await client.post(
                "/api/v1/wallet/recharges",
                json={"amount": "50.00", "channel": "mock"},
                headers=headers,
            )
            assert resp.status_code == 201, resp.text
        finally:
            settings.real_name_required_for_recharge = False

    async def test_register_requires_terms(self, client: AsyncClient):
        await client.post(
            "/api/v1/auth/sms-code", json={"phone": "13800000163", "purpose": "register"}
        )
        resp = await client.post(
            "/api/v1/auth/register", json={"phone": "13800000163", "sms_code": "123456"}
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "TERMS_NOT_ACCEPTED"
