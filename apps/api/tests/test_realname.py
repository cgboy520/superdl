"""实名认证:安全策略 real_name_enabled 开关、provider seam(注入假渠道)、核验通过/不一致/
重复提交/脱敏入库、充值与开通实例的强制门禁。"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import get_settings
from app.modules.account.models import User
from app.modules.account.realname import set_realname_provider
from tests.helpers import (
    create_test_sku,
    funded_user,
    seed_instance,
    seed_node_spec,
    set_platform_setting,
    user_headers_with_id,
)


class _Provider:
    def __init__(self, ok: bool) -> None:
        self.ok = ok

    async def verify(self, name: str, id_number: str, phone: str) -> bool:
        return self.ok


@pytest.fixture(autouse=True)
def _reset_provider():
    yield
    set_realname_provider(None)


class TestRealName:
    async def test_verify_success_masks_id_number(self, client: AsyncClient, sm):
        await set_platform_setting(sm, "real_name_enabled", "true")
        set_realname_provider(_Provider(True))
        headers, user_id = await user_headers_with_id(client, "13800000160")
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
        assert resp.status_code == 409
        assert resp.json()["code"] == "CONFLICT"

    async def test_mismatch_rejected(self, client: AsyncClient, sm):
        await set_platform_setting(sm, "real_name_enabled", "true")
        set_realname_provider(_Provider(False))
        headers, _ = await user_headers_with_id(client, "13800000161")
        resp = await client.post(
            "/api/v1/me/real-name",
            json={"name": "李四", "id_number": "110101199001010000"},
            headers=headers,
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "REAL_NAME_MISMATCH"

    async def test_disabled_is_409_without_touching_provider(self, client: AsyncClient):
        """开关关闭(默认):409,渠道不被调用。"""
        set_realname_provider(_Provider(True))
        headers, _ = await user_headers_with_id(client, "13800000166")
        resp = await client.post(
            "/api/v1/me/real-name",
            json={"name": "张三", "id_number": "110101199001011234"},
            headers=headers,
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "REAL_NAME_DISABLED"

    async def test_enabled_without_credentials_is_502_not_500(self, client: AsyncClient, sm):
        """开关开启但凭据未配置:RealNameError → 502 渠道故障。"""
        await set_platform_setting(sm, "real_name_enabled", "true")
        headers, _ = await user_headers_with_id(client, "13800000164")
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
            headers, _ = await user_headers_with_id(client, "13800000162")
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
        """强制实名开启时算力开通同样拦截。"""
        settings = get_settings()
        settings.real_name_enabled = True
        settings.real_name_required_for_recharge = True
        set_realname_provider(_Provider(True))
        try:
            headers, _user_id, key_id = await funded_user(client, sm, "13800000165")
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

    async def test_start_and_disk_gates_when_required(self, client: AsyncClient, sm):
        """开机与建盘同闸。"""
        settings = get_settings()
        settings.real_name_enabled = True
        settings.real_name_required_for_recharge = True
        set_realname_provider(_Provider(True))
        try:
            headers, user_id, _key_id = await funded_user(client, sm, "13800000166")
            await create_test_sku(sm)
            await seed_node_spec(sm)
            # 建盘:未实名 403
            resp = await client.post(
                "/api/v1/disks", json={"name": "d1", "size_gb": 10}, headers=headers
            )
            assert resp.status_code == 403
            assert resp.json()["message_key"] == "disks.realNameRequired"
            # 开机:未实名 403(直接 seed 一台 stopped)
            _, uuid = await seed_instance(sm, user_id=user_id, status="stopped")
            resp = await client.post(f"/api/v1/instances/{uuid}/start", headers=headers)
            assert resp.status_code == 403
            assert resp.json()["message_key"] == "orchestrator.realNameRequired"
            # 实名后两路放行
            await client.post(
                "/api/v1/me/real-name",
                json={"name": "钱七", "id_number": "110101199001013333"},
                headers=headers,
            )
            resp = await client.post(
                "/api/v1/disks", json={"name": "d1", "size_gb": 10}, headers=headers
            )
            assert resp.status_code == 201, resp.text
            resp = await client.post(f"/api/v1/instances/{uuid}/start", headers=headers)
            assert resp.status_code == 200, resp.text
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
