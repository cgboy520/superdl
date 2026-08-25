"""平台配置中心:加密往返、白名单校验、脱敏读取、覆盖即时生效、渠道开关门禁、角色隔离。"""

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core import crypto
from app.core.platform_config import (
    PlatformSetting,
    validate_setting_value,
)
from app.modules.account.realname import (
    AliyunRealNameProvider,
    RealNameError,
    get_realname_provider,
)
from tests.test_catalog import admin_headers


class TestCrypto:
    def test_roundtrip_and_prefix(self):
        token = crypto.encrypt_str("AKSECRET-12345678", aad="sms_access_key_secret")
        assert token.startswith("enc:v1:")
        assert "AKSECRET" not in token
        assert crypto.decrypt_str(token, aad="sms_access_key_secret") == "AKSECRET-12345678"

    def test_wrong_aad_rejected(self):
        """AAD 绑定键名:密文不可跨字段搬运。"""
        from cryptography.exceptions import InvalidTag

        token = crypto.encrypt_str("secret", aad="sms_access_key_secret")
        with pytest.raises(InvalidTag):
            crypto.decrypt_str(token, aad="wechat_apiv3_key")


class TestSpecValidation:
    def test_unknown_key_and_javascript_url_rejected(self):
        """白名单是防线:未知键一律拒;亮照链接会被页脚渲染成 <a href>,只收 http(s) 绝对 URL
        (留空走「清除覆盖」分支不经此校验);值一律 strip 后落库。"""
        with pytest.raises(ValueError, match="未知配置键"):
            validate_setting_value("jwt_secret", "x")
        with pytest.raises(ValueError):
            validate_setting_value("business_license_url", "javascript:alert(1)")
        assert (
            validate_setting_value("business_license_url", " https://example.com/l.png ")
            == "https://example.com/l.png"
        )


class TestAdminApi:
    async def test_admin_only(self, client: AsyncClient, sm):
        """渠道凭据不下放:ops / finance / readonly 一律 403。"""
        for role in ("ops", "finance", "readonly"):
            ah = await admin_headers(sm, client, role=role)
            assert (
                await client.get("/api/admin/v1/platform-config", headers=ah)
            ).status_code == 403
            resp = await client.put(
                "/api/admin/v1/platform-config",
                json={"updates": {"icp_number": "x"}, "reason": "test"},
                headers=ah,
            )
            assert resp.status_code == 403

    async def test_get_masks_secret_and_put_overrides(self, client: AsyncClient, sm):
        """管理端写入 → GET 脱敏回读 → 公开 site-config 透出(备案号与经营主体四项,
        《电子商务法》第十五条公示)→ 空串清除覆盖回退 env 默认。"""
        ah = await admin_headers(sm, client, role="admin")
        resp = await client.put(
            "/api/admin/v1/platform-config",
            json={
                "updates": {
                    "icp_number": "京ICP备2026012345号-1",
                    "sms_sign_name": "SuperDL",
                    "sms_access_key_secret": "PLAINTEXT-SECRET-9876",
                    "company_name": "示例云算力(北京)有限公司",
                    "company_address": "北京市海淀区示例路 1 号",
                    "company_phone": "010-12345678",
                    "business_license_url": "https://example.com/license.png",
                },
                "reason": "上线前配置",
            },
            headers=ah,
        )
        assert resp.status_code == 200, resp.text

        data = (await client.get("/api/admin/v1/platform-config", headers=ah)).json()
        items = {i["key"]: i for i in data["items"]}
        assert items["icp_number"]["value"] == "京ICP备2026012345号-1"
        assert items["icp_number"]["source"] == "override"
        assert items["company_name"]["group"] == "compliance"
        # secret:GET 永不回明文,只回状态与尾 4 位预览
        secret_item = items["sms_access_key_secret"]
        assert secret_item["value"] is None
        assert secret_item["configured"] is True
        assert secret_item["preview"] == "****9876"
        assert "PLAINTEXT-SECRET-9876" not in str(data)

        # DB 落的是密文,不是明文
        async with sm() as session:
            row = (
                await session.execute(
                    select(PlatformSetting).where(PlatformSetting.key == "sms_access_key_secret")
                )
            ).scalar_one()
            assert row.value.startswith("enc:v1:")
            assert "PLAINTEXT" not in row.value

        # 公开 site-config 跟随备案号与经营主体
        site = (await client.get("/api/v1/site-config")).json()
        assert site["icp_number"] == "京ICP备2026012345号-1"
        assert site["company_name"] == "示例云算力(北京)有限公司"
        assert site["company_address"] == "北京市海淀区示例路 1 号"
        assert site["company_phone"] == "010-12345678"
        assert site["business_license_url"] == "https://example.com/license.png"
        assert site["payment_channels"] == {"wechat": False, "alipay": False, "mock": True}

        # 空串 = 清除覆盖,回退 env 默认(None → 页脚不显示该行)
        await client.put(
            "/api/admin/v1/platform-config",
            json={
                "updates": {"icp_number": "", "business_license_url": ""},
                "reason": "清除测试",
            },
            headers=ah,
        )
        site = (await client.get("/api/v1/site-config")).json()
        assert site["icp_number"] is None
        assert site["business_license_url"] is None

    async def test_unknown_key_rejected_via_api(self, client: AsyncClient, sm):
        """白名单是防线:管理端拿不到写任意配置(如 JWT 密钥)的口子。"""
        ah = await admin_headers(sm, client, role="admin")
        resp = await client.put(
            "/api/admin/v1/platform-config",
            json={"updates": {"jwt_secret": "hack"}, "reason": "attack"},
            headers=ah,
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "VALIDATION_ERROR"

    async def test_prod_rejects_forced_realname_with_mock_provider(
        self, client: AsyncClient, sm, monkeypatch
    ):
        """prod 下「充值强制实名 + mock 渠道」组合由 DB 写入侧拦(启动校验只看 env 层):
        mock 核验恒过,开着强制实名等于没有实名。两个方向都拦;同一批切成 aliyun 则放行。"""
        from app.core.config import get_settings

        ah = await admin_headers(sm, client, role="admin")
        settings = get_settings()
        monkeypatch.setattr(settings, "environment", "prod", raising=False)
        ah["Host"] = settings.admin_host  # prod 边缘收口只放行 admin 域

        async def put(updates: dict[str, str]):
            return await client.put(
                "/api/admin/v1/platform-config",
                json={"updates": updates, "reason": "合规开启"},
                headers=ah,
            )

        resp = await put({"real_name_required_for_recharge": "true"})
        assert resp.status_code == 400, resp.text
        assert resp.json()["code"] == "VALIDATION_ERROR"
        assert "real_name_provider" in resp.json()["message"]
        # 同一批把渠道切成 aliyun:组合终态合法
        resp = await put(
            {"real_name_required_for_recharge": "true", "real_name_provider": "aliyun"}
        )
        assert resp.status_code == 200, resp.text
        # 反向:强制实名开着,再把渠道切回 mock 同样被拒
        assert (await put({"real_name_provider": "mock"})).status_code == 400

    async def test_real_name_flag_flows_to_policies_and_gate(self, client: AsyncClient, sm):
        """开关走平台配置:公开 policies 即时跟随,充值门禁即时生效(免重启)。"""
        from tests.test_payment import user_headers

        ah = await admin_headers(sm, client, role="admin")
        base = (await client.get("/api/v1/policies")).json()
        assert base["real_name_required_for_recharge"] is False

        await client.put(
            "/api/admin/v1/platform-config",
            json={"updates": {"real_name_required_for_recharge": "true"}, "reason": "合规开启"},
            headers=ah,
        )
        assert (await client.get("/api/v1/policies")).json()[
            "real_name_required_for_recharge"
        ] is True

        headers = await user_headers(client, "13700000201")
        resp = await client.post(
            "/api/v1/wallet/recharges",
            json={"amount": "50.00", "channel": "mock"},
            headers=headers,
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "REAL_NAME_REQUIRED"


class TestChannelGate:
    async def test_disabled_channel_rejected(self, client: AsyncClient, sm):
        """渠道开关默认关:未开通渠道下单被拒;开通但凭据不全同样拒(不产生脏单)。"""
        from tests.test_payment import user_headers

        headers = await user_headers(client, "13700000202")
        resp = await client.post(
            "/api/v1/wallet/recharges",
            json={"amount": "50.00", "channel": "wechat"},
            headers=headers,
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "PAYMENT_CHANNEL_ERROR"
        assert resp.json()["message_key"] == "billing.channelNotEnabled"

        ah = await admin_headers(sm, client, role="admin")
        await client.put(
            "/api/admin/v1/platform-config",
            json={"updates": {"payment_wechat_enabled": "true"}, "reason": "联调"},
            headers=ah,
        )
        resp = await client.post(
            "/api/v1/wallet/recharges",
            json={"amount": "50.00", "channel": "wechat"},
            headers=headers,
        )
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "billing.wechatCredentialsIncomplete"


class TestAliyunRealNameProvider:
    def _provider(self, handler) -> AliyunRealNameProvider:
        return AliyunRealNameProvider("ak", "sk", transport=httpx.MockTransport(handler))

    async def test_bizcode_mapping(self):
        responses = iter(
            [
                {"Code": "200", "ResultObject": {"BizCode": "1"}},
                {"Code": "200", "ResultObject": {"BizCode": "2"}},
                {"Code": "200", "ResultObject": {"BizCode": "3"}},
                {"Code": "403", "Message": "Forbidden.RAM"},
            ]
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=next(responses))

        provider = self._provider(handler)
        assert await provider.verify("张三", "110101199001011234", "13800000000") is True
        assert await provider.verify("张三", "110101199001011234", "13800000000") is False
        assert await provider.verify("张三", "110101199001011234", "13800000000") is False
        with pytest.raises(RealNameError, match="403"):
            await provider.verify("张三", "110101199001011234", "13800000000")

    async def test_factory_switches_by_config(self, client: AsyncClient, sm):
        ah = await admin_headers(sm, client, role="admin")
        await client.put(
            "/api/admin/v1/platform-config",
            json={
                "updates": {
                    "real_name_provider": "aliyun",
                    "real_name_access_key_id": "LTAI5tTESTTESTTEST",
                    "real_name_access_key_secret": "sk-test-secret",
                },
                "reason": "接入阿里云实名",
            },
            headers=ah,
        )
        async with sm() as session:
            provider = await get_realname_provider(session)
        assert isinstance(provider, AliyunRealNameProvider)


class TestEffectiveConfig:
    async def test_corrupt_secret_row_falls_back_to_env(self, sm):
        """单行密文损坏(主密钥换错/手工改库)只让该键回落 env,不得拖垮整份配置。"""
        from app.core.platform_config import PlatformSetting, get_effective_platform_config

        async with sm() as session:
            session.add(
                PlatformSetting(
                    key="sms_access_key_secret", value="enc:v1:corrupt", updated_by=None
                )
            )
            await session.commit()
        async with sm() as session:
            cfg = await get_effective_platform_config(session)  # 不抛
        assert cfg["sms_access_key_secret"] == ""  # env 未设 → 空串
