"""平台配置中心:加密往返、白名单校验、脱敏读取、覆盖即时生效、渠道开关门禁、角色隔离。"""

from types import SimpleNamespace

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
from tests.helpers import admin_headers


class TestCrypto:
    def test_roundtrip_and_prefix(self):
        token = crypto.encrypt_str("AKSECRET-12345678", aad="sms_access_key_secret")
        assert token.startswith("enc:v2:")
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


class TestProdDegradeForbidden:
    """降防开关(人机验证/管理端 MFA/实名)在 prod 禁止在线关闭。

    单管理员一次请求即降防的口子必须堵死 —— 关掉 MFA 连 MFA 自身的保护也一并消失
    (自我解除);env/部署层保留(env 层 prod 关闭启动时只告警),在线写库层一律禁。
    """

    @pytest.mark.parametrize("key", ["captcha_enabled", "admin_mfa_enabled", "real_name_enabled"])
    def test_security_switches_cannot_be_disabled_in_prod(self, key, monkeypatch):
        monkeypatch.setattr(
            "app.core.platform_config.get_settings",
            lambda: SimpleNamespace(environment="prod"),
        )
        with pytest.raises(ValueError, match="生产环境禁止"):
            validate_setting_value(key, "false")
        # 开启(升防)不受限
        assert validate_setting_value(key, "true") == "true"

    @pytest.mark.parametrize("key", ["captcha_enabled", "admin_mfa_enabled", "real_name_enabled"])
    def test_security_switches_toggle_freely_outside_prod(self, key, monkeypatch):
        monkeypatch.setattr(
            "app.core.platform_config.get_settings",
            lambda: SimpleNamespace(environment="dev"),
        )
        assert validate_setting_value(key, "false") == "false"


class TestProdComplianceGates:
    """D-4 启动 fail-fast(兜 env/部署层;在线写库层由上面的 prod_forbidden 禁关)。"""

    def test_prod_refuses_boot_with_switches_off(self):
        from app.core.platform_config import assert_prod_compliance_gates

        off = {
            "captcha_enabled": "false",
            "real_name_enabled": "false",
            "real_name_required_for_recharge": "false",
        }
        with pytest.raises(RuntimeError, match="合规开关未全开"):
            assert_prod_compliance_gates(off, "prod")
        # 只开一部分同样拒(报错带缺项键名)
        with pytest.raises(RuntimeError, match="real_name_enabled"):
            assert_prod_compliance_gates(dict(off, captcha_enabled="true"), "prod")

    def test_prod_boots_with_all_on_and_non_prod_unaffected(self):
        from app.core.platform_config import assert_prod_compliance_gates

        on = {
            "captcha_enabled": "true",
            "real_name_enabled": "true",
            "real_name_required_for_recharge": "true",
        }
        assert_prod_compliance_gates(on, "prod")  # 不抛
        assert_prod_compliance_gates({}, "dev")  # 非 prod 一律放行
        assert_prod_compliance_gates({}, "test")


class TestAdminApi:
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
            assert row.value.startswith("enc:v2:")
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

    async def test_required_real_name_needs_enabled_in_any_env(self, client: AsyncClient, sm):
        """「充值强制实名」必须伴随「实名认证已开启」,test 环境同样拦(不是 prod 专属):
        实名未开通时用户永远完不成实名,充值会被永久卡住。两个方向都拦;同一批一起开则放行。"""
        ah = await admin_headers(sm, client, role="admin")

        async def put(updates: dict[str, str]):
            return await client.put(
                "/api/admin/v1/platform-config",
                json={"updates": updates, "reason": "合规开启"},
                headers=ah,
            )

        resp = await put({"real_name_required_for_recharge": "true"})
        assert resp.status_code == 400, resp.text
        assert resp.json()["code"] == "VALIDATION_ERROR"
        assert "real_name_enabled" in resp.json()["message"]
        # 同一批一起开:组合终态合法
        resp = await put({"real_name_required_for_recharge": "true", "real_name_enabled": "true"})
        assert resp.status_code == 200, resp.text
        # 反向:强制实名开着,再关实名认证同样被拒
        assert (await put({"real_name_enabled": "false"})).status_code == 400

    async def test_real_name_flag_flows_to_policies_and_gate(self, client: AsyncClient, sm):
        """开关走平台配置:公开 policies 即时跟随,充值门禁即时生效(免重启)。"""
        from tests.helpers import user_headers

        ah = await admin_headers(sm, client, role="admin")
        base = (await client.get("/api/v1/policies")).json()
        assert base["real_name_enabled"] is False
        assert base["real_name_required_for_recharge"] is False

        resp = await client.put(
            "/api/admin/v1/platform-config",
            json={
                "updates": {"real_name_enabled": "true", "real_name_required_for_recharge": "true"},
                "reason": "合规开启",
            },
            headers=ah,
        )
        assert resp.status_code == 200, resp.text
        policies = (await client.get("/api/v1/policies")).json()
        assert policies["real_name_enabled"] is True
        assert policies["real_name_required_for_recharge"] is True

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
        from tests.helpers import user_headers

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

    async def test_factory_builds_aliyun_from_config(self, client: AsyncClient, sm):
        """凭据经管理端录入后,工厂按生效配置构造阿里云渠道
        (无凭据时抛 RealNameError,见 test_realname)。"""
        ah = await admin_headers(sm, client, role="admin")
        await client.put(
            "/api/admin/v1/platform-config",
            json={
                "updates": {
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


class TestRegistrySpecsAndProbeEndpoint:
    def test_registry_key_validation(self):
        """host 不带 scheme、机器人名带 robot$ 前缀、代理映射逐行 <上游>=<项目>:
        格式错在录入时就拒。"""
        assert (
            validate_setting_value("registry_host", " harbor.example.com:8443 ")
            == "harbor.example.com:8443"
        )
        with pytest.raises(ValueError):
            validate_setting_value("registry_host", "https://harbor.example.com")
        assert (
            validate_setting_value("registry_robot_name", "robot$superdl+pull")
            == "robot$superdl+pull"
        )
        with pytest.raises(ValueError):
            validate_setting_value("registry_robot_name", "admin")
        assert (
            validate_setting_value("registry_proxy_projects", "docker.io=dockerhub\nghcr.io=ghcr")
            == "docker.io=dockerhub\nghcr.io=ghcr"
        )
        with pytest.raises(ValueError):
            validate_setting_value("registry_proxy_projects", "docker.io")
        with pytest.raises(ValueError):
            validate_setting_value("registry_ca_pem", "not a pem")

    def test_multiline_specs_reject_evil_line_and_linear_time(self):
        """多行 text 配置逐行锚定校验:非法行被拒;超长对抗输入必须线性时间返回
        (回归:嵌套量词整串匹配曾使 ~40 字符的输入即可挂死事件循环,ReDoS)。"""
        import time

        assert validate_setting_value(
            "image_allowed_registries", "docker.io/\nregistry.example.com/team/"
        )
        with pytest.raises(ValueError, match="含非法行"):
            validate_setting_value("image_allowed_registries", "docker.io/\nBAD HOST!!")
        # 对抗输入:全部合法字符 + 一个非法尾字符(旧正则在此外爆)
        evil = "a" * 4000 + "!"
        t0 = time.perf_counter()
        with pytest.raises(ValueError):
            validate_setting_value("image_allowed_registries", evil)
        assert time.perf_counter() - t0 < 1.0  # 线性;旧实现为指数级(数十分钟级)
        # 合法长输入同样线性放行
        t0 = time.perf_counter()
        assert validate_setting_value("image_allowed_registries", "a" * 4000)
        assert time.perf_counter() - t0 < 1.0

    async def test_probe_endpoint_requires_host_then_reports_probe(
        self, client: AsyncClient, sm, monkeypatch
    ):
        from app.core.registry import HarborProbe
        from app.modules.adminapi import router_ops

        ah = await admin_headers(sm, client, role="admin")
        resp = await client.post("/api/admin/v1/platform-config/test-registry", headers=ah)
        assert resp.status_code == 400  # 未填 host:不发探测

        async def fake_probe(**kwargs):
            assert kwargs["host"] == "harbor.example.com" and kwargs["project"] == "superdl"
            return HarborProbe(True, "done", "ok", "v2.12.0", 3)

        monkeypatch.setattr(router_ops, "probe_harbor", fake_probe)
        await client.put(
            "/api/admin/v1/platform-config",
            json={"updates": {"registry_host": "harbor.example.com"}, "reason": "接入 Harbor"},
            headers=ah,
        )
        resp = await client.post("/api/admin/v1/platform-config/test-registry", headers=ah)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "ok": True,
            "step": "done",
            "detail": "ok",
            "harbor_version": "v2.12.0",
            "repositories": 3,
        }
        # 非 admin 角色不可探测(凭据不下放 ops)
        ops = await admin_headers(sm, client, role="ops")
        assert (
            await client.post("/api/admin/v1/platform-config/test-registry", headers=ops)
        ).status_code == 403


class TestConfigWarnings:
    def test_rules_by_switch_and_credentials(self):
        """每条规则一例:挂了说明配置页红牌 / 启动告警与实际风险漂移
        (开关关了没人提醒、开了没凭据在 502 却显示正常)。"""
        from app.core.platform_config import SETTING_SPECS, compute_config_warnings

        base = dict.fromkeys(SETTING_SPECS, "")
        base.update(
            captcha_enabled="false",
            admin_mfa_enabled="true",
            real_name_enabled="false",
            registry_host="harbor.example.com",  # Harbor 地址自动进白名单,不触发规则 6
        )
        assert compute_config_warnings(base, "test") == []
        # 镜像仓库规则:填了机器人未填 Secret → error;prod 下无白名单且无 Harbor 地址 → warning
        robot_only = dict(base, registry_robot_name="robot$superdl+pull")
        assert [(w.key, w.level) for w in compute_config_warnings(robot_only, "test")] == [
            ("registry_robot_name", "error")
        ]
        no_registry = dict(
            base,
            registry_host="",
            captcha_enabled="true",
            captcha_scene_id="s",
            captcha_access_key_id="LTAI5tTESTTESTTEST",
            captcha_access_key_secret="k",
        )
        assert [(w.key, w.level) for w in compute_config_warnings(no_registry, "prod")] == [
            ("image_allowed_registries", "warning")
        ]
        assert [(w.key, w.level) for w in compute_config_warnings(base, "prod")] == [
            ("captcha_enabled", "error")
        ]
        on = dict(base, captcha_enabled="true", admin_mfa_enabled="false", real_name_enabled="true")
        keys = {(w.key, w.level) for w in compute_config_warnings(on, "prod")}
        assert keys == {
            ("captcha_enabled", "error"),
            ("admin_mfa_enabled", "warning"),
            ("real_name_enabled", "error"),
        }
        full = dict(
            on,
            captcha_scene_id="scene",
            captcha_access_key_id="LTAI5tTESTTESTTEST",
            captcha_access_key_secret="sk",
            real_name_access_key_id="LTAI5tTESTTESTTEST",
            real_name_access_key_secret="sk",
        )
        assert [w.key for w in compute_config_warnings(full, "prod")] == ["admin_mfa_enabled"]
        assert compute_config_warnings(dict(full, admin_mfa_enabled="true"), "prod") == []

    async def test_api_exposes_warnings(self, client: AsyncClient, sm):
        """开启人机验证而未录凭据:GET /platform-config 的 warnings 立即带 error 级提示。"""
        ah = await admin_headers(sm, client, role="admin")
        assert (await client.get("/api/admin/v1/platform-config", headers=ah)).json()[
            "warnings"
        ] == []
        resp = await client.put(
            "/api/admin/v1/platform-config",
            json={"updates": {"captcha_enabled": "true"}, "reason": "先开开关"},
            headers=ah,
        )
        assert resp.status_code == 200, resp.text
        warnings = (await client.get("/api/admin/v1/platform-config", headers=ah)).json()[
            "warnings"
        ]
        assert [(w["key"], w["level"]) for w in warnings] == [("captcha_enabled", "error")]


class TestEffectiveConfig:
    async def test_corrupt_secret_row_fails_closed(self, sm):
        """单行密文损坏(主密钥换错/手工改库)fail-closed:抛错而不是静默回落 env——
        轮换窗口里回落等于悄悄用回旧值(审计 #18)。"""
        from app.core.platform_config import PlatformSetting, get_effective_platform_config

        async with sm() as session:
            session.add(
                PlatformSetting(
                    key="sms_access_key_secret", value="enc:v1:corrupt", updated_by=None
                )
            )
            await session.commit()
        async with sm() as session:
            with pytest.raises(ValueError, match="解密失败"):
                await get_effective_platform_config(session)
