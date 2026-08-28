"""实名认证:安全策略 real_name_enabled 开关、provider seam(注入假渠道)、核验通过/不一致/
重复提交/脱敏入库、充值与开通实例的强制门禁。"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import get_settings
from app.core.platform_config import PlatformSetting
from app.modules.account.models import User
from app.modules.account.realname import set_realname_provider
from tests.helpers import register


class _Provider:
    def __init__(self, ok: bool) -> None:
        self.ok = ok

    async def verify(self, name: str, id_number: str, phone: str) -> bool:
        return self.ok


@pytest.fixture(autouse=True)
def _reset_provider():
    yield
    set_realname_provider(None)


async def _enable(sm) -> None:
    async with sm() as session:
        session.add(PlatformSetting(key="real_name_enabled", value="true"))
        await session.commit()


async def _headers(client: AsyncClient, phone: str) -> tuple[dict, int]:
    data = await register(client, phone)
    return {"Authorization": f"Bearer {data['access_token']}"}, data["user"]["id"]


class TestRealName:
    async def test_verify_success_masks_id_number(self, client: AsyncClient, sm):
        await _enable(sm)
        set_realname_provider(_Provider(True))
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

    async def test_mismatch_rejected(self, client: AsyncClient, sm):
        await _enable(sm)
        set_realname_provider(_Provider(False))
        headers, _ = await _headers(client, "13800000161")
        resp = await client.post(
            "/api/v1/me/real-name",
            json={"name": "李四", "id_number": "110101199001010000"},
            headers=headers,
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "REAL_NAME_MISMATCH"

    async def test_disabled_is_409_without_touching_provider(self, client: AsyncClient):
        """开关关闭(默认):明确 409,渠道不被调用——挂了说明关闭没有短路提交路径。"""
        set_realname_provider(_Provider(True))
        headers, _ = await _headers(client, "13800000166")
        resp = await client.post(
            "/api/v1/me/real-name",
            json={"name": "张三", "id_number": "110101199001011234"},
            headers=headers,
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "REAL_NAME_DISABLED"

    async def test_enabled_without_credentials_is_502_not_500(self, client: AsyncClient, sm):
        """开关开启但凭据未配置:取 provider 即抛 RealNameError,必须走设计好的 502
        渠道故障(与 verify 失败同径),不能漏成 500。"""
        await _enable(sm)
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
        settings.real_name_enabled = True
        settings.real_name_required_for_recharge = True
        set_realname_provider(_Provider(True))
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
            settings.real_name_enabled = False

    async def test_create_instance_gate_when_required(self, client: AsyncClient, sm):
        """强制实名开启时:算力开通同样拦截(监管对算力服务的要求不低于预收款);
        只拦充值的话,匿名账号可绕过实名直接租 GPU。"""
        from tests.helpers import create_test_sku, create_user_with_key, fund_wallet, seed_node_spec

        settings = get_settings()
        settings.real_name_enabled = True
        settings.real_name_required_for_recharge = True
        set_realname_provider(_Provider(True))
        try:
            headers, user_id, key_id = await create_user_with_key(client, "13800000165")
            await fund_wallet(sm, user_id)
            sku_id = await create_test_sku(sm)
            await seed_node_spec(sm)
            resp = await client.post(
                "/api/v1/instances",
                json={"sku_id": sku_id, "image_ref": "img", "ssh_key_ids": [key_id]},
                headers=headers,
            )
            assert resp.status_code == 403
            assert resp.json()["code"] == "REAL_NAME_REQUIRED"
            assert resp.json()["message_key"] == "orchestrator.realNameRequired"

            # 完成实名后放行(202 异步受理)
            await client.post(
                "/api/v1/me/real-name",
                json={"name": "赵六", "id_number": "110101199001012222"},
                headers=headers,
            )
            resp = await client.post(
                "/api/v1/instances",
                json={"sku_id": sku_id, "image_ref": "img", "ssh_key_ids": [key_id]},
                headers=headers,
            )
            assert resp.status_code == 202, resp.text
        finally:
            settings.real_name_required_for_recharge = False
            settings.real_name_enabled = False

    async def test_register_requires_terms(self, client: AsyncClient):
        await client.post(
            "/api/v1/auth/sms-code",
            json={"phone": "13800000163", "purpose": "register"},
        )
        resp = await client.post(
            "/api/v1/auth/register", json={"phone": "13800000163", "sms_code": "123456"}
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "TERMS_NOT_ACCEPTED"
