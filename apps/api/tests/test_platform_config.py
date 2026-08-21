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

    def test_bad_prefix_rejected(self):
        with pytest.raises(ValueError):
            crypto.decrypt_str("plaintext-not-encrypted", aad="x")


class TestSpecValidation:
    def test_unknown_key_rejected(self):
        """白名单是防线:管理端拿不到写任意配置(如 JWT 密钥)的口子。"""
        with pytest.raises(ValueError, match="未知配置键"):
            validate_setting_value("jwt_secret", "hack")

    def test_kind_and_pattern_checks(self):
        with pytest.raises(ValueError):
            validate_setting_value("payment_wechat_enabled", "yes")
        with pytest.raises(ValueError):
            validate_setting_value("sms_provider", "tencent")
        with pytest.raises(ValueError):
            validate_setting_value("sms_template_verify", "TPL_123")  # 须 SMS_ 前缀
        with pytest.raises(ValueError):
            validate_setting_value("wechat_apiv3_key", "short")
        with pytest.raises(ValueError, match="不含 -----BEGIN"):
            validate_setting_value("alipay_private_key", "-----BEGIN PRIVATE KEY-----MIIE")
        assert (
            validate_setting_value("icp_number", " 京ICP备2026012345号-1 ")
            == "京ICP备2026012345号-1"
        )
        assert validate_setting_value("sms_template_verify", "SMS_123456789") == "SMS_123456789"


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
        ah = await admin_headers(sm, client, role="admin")
        resp = await client.put(
            "/api/admin/v1/platform-config",
            json={
                "updates": {
                    "icp_number": "京ICP备2026012345号-1",
                    "sms_sign_name": "SuperDL",
                    "sms_access_key_secret": "PLAINTEXT-SECRET-9876",
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

        # 公开 site-config 跟随备案号
        site = (await client.get("/api/v1/site-config")).json()
        assert site["icp_number"] == "京ICP备2026012345号-1"
        assert site["payment_channels"] == {"wechat": False, "alipay": False, "mock": True}

        # 空串 = 清除覆盖,回退 env 默认(None → 页脚不显示)
        await client.put(
            "/api/admin/v1/platform-config",
            json={"updates": {"icp_number": ""}, "reason": "清除测试"},
            headers=ah,
        )
        site = (await client.get("/api/v1/site-config")).json()
        assert site["icp_number"] is None

    async def test_unknown_key_rejected_via_api(self, client: AsyncClient, sm):
        ah = await admin_headers(sm, client, role="admin")
        resp = await client.put(
            "/api/admin/v1/platform-config",
            json={"updates": {"jwt_secret": "hack"}, "reason": "attack"},
            headers=ah,
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "VALIDATION_ERROR"

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


class TestSmsTestSend:
    async def test_test_sms_via_injected_channel(self, client: AsyncClient, sm):
        from app.core.sms import set_sms_channel

        sent: list[tuple[str, str]] = []

        class _Recorder:
            async def send(self, phone: str, template: str, params: dict[str, str]) -> None:
                sent.append((phone, template))

        set_sms_channel(_Recorder())
        try:
            ah = await admin_headers(sm, client, role="admin")
            resp = await client.post(
                "/api/admin/v1/platform-config/test-sms",
                json={"phone": "13800000199"},
                headers=ah,
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["ok"] is True
            assert sent and sent[0][0] == "13800000199"
        finally:
            set_sms_channel(None)


class TestAliyunRealNameProvider:
    def _provider(self, handler) -> AliyunRealNameProvider:
        return AliyunRealNameProvider("ak", "sk", transport=httpx.MockTransport(handler))

    def test_signed_params_shape(self):
        p = AliyunRealNameProvider("ak", "sk").signed_params(
            "张三",
            "110101199001011234",
            "13800000000",
            nonce="fixed-nonce",
            timestamp="2026-08-19T12:00:00Z",
        )
        assert p["Action"] == "Mobile3MetaSimpleVerify"
        assert p["Version"] == "2019-03-07"
        assert p["ParamType"] == "normal"
        assert p["UserName"] == "张三"
        assert p["IdentifyNum"] == "110101199001011234"
        assert p["Mobile"] == "13800000000"
        assert p["SignatureMethod"] == "HMAC-SHA1"
        assert p["Signature"]

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
