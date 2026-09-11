"""平台配置中心:白名单校验、脱敏读取、覆盖即时生效、渠道开关门禁、角色隔离。"""

from types import SimpleNamespace

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.platform_config import (
    PlatformSetting,
    validate_setting_value,
)
from app.modules.account.realname import (
    AliyunRealNameProvider,
    RealNameError,
    get_realname_provider,
)
from tests.helpers import admin_headers, user_headers


class TestSpecValidation:
    def test_unknown_key_and_javascript_url_rejected(self):
        """未知键拒;亮照链接只收 http(s) 绝对 URL(留空走清除覆盖);值 strip 后落库。"""
        with pytest.raises(ValueError, match="未知配置键"):
            validate_setting_value("jwt_secret", "x")
        with pytest.raises(ValueError):
            validate_setting_value("business_license_url", "javascript:alert(1)")
        assert (
            validate_setting_value("business_license_url", " https://example.com/l.png ")
            == "https://example.com/l.png"
        )


class TestProdDegradeForbidden:
    """降防开关(人机验证/管理端 MFA/实名)在 prod 禁止在线关闭。"""

    @pytest.mark.parametrize("key", ["captcha_enabled", "admin_mfa_enabled", "real_name_enabled"])
    def test_security_switches_cannot_be_disabled_in_prod(self, key, monkeypatch):
        monkeypatch.setattr(
            "app.core.platform_config.get_settings",
            lambda: SimpleNamespace(environment="prod"),
        )
        with pytest.raises(ValueError, match="生产环境禁止"):
            validate_setting_value(key, "false")
        # 开启不受限
        assert validate_setting_value(key, "true") == "true"

    @pytest.mark.parametrize("key", ["captcha_enabled", "admin_mfa_enabled", "real_name_enabled"])
    def test_security_switches_toggle_freely_outside_prod(self, key, monkeypatch):
        monkeypatch.setattr(
            "app.core.platform_config.get_settings",
            lambda: SimpleNamespace(environment="dev"),
        )
        assert validate_setting_value(key, "false") == "false"


class TestClearOverrideFallbackGuard:
    """清除覆盖 = 回落到部署层(env)取值;回落值是 prod 禁止取值时同拦。"""

    async def test_clear_rejected_when_env_fallback_is_prod_forbidden(
        self, client: AsyncClient, sm, monkeypatch
    ):
        """部署层 real_name_enabled=false(prod 禁止值)→ 清除覆盖被拒。"""
        ah = await admin_headers(sm, client, role="admin")
        # 先取令牌再换桩
        monkeypatch.setattr(
            "app.core.platform_config.get_settings",
            lambda: SimpleNamespace(environment="prod", real_name_enabled=False),
        )
        resp = await client.put(
            "/api/admin/v1/platform-config",
            json={"updates": {"real_name_enabled": ""}, "reason": "清除覆盖"},
            headers=ah,
        )
        assert resp.status_code == 400
        assert "不允许清除覆盖" in resp.json()["message"]
        # 拒绝即不落库
        async with sm() as session:
            row = (
                await session.execute(
                    select(PlatformSetting).where(PlatformSetting.key == "real_name_enabled")
                )
            ).scalar_one_or_none()
            assert row is None

    async def test_clear_allowed_when_env_fallback_is_compliant(
        self, client: AsyncClient, sm, monkeypatch
    ):
        """部署层已是合规值(captcha_enabled=true)→ 清除覆盖放行。"""
        ah = await admin_headers(sm, client, role="admin")
        monkeypatch.setattr(
            "app.core.platform_config.get_settings",
            lambda: SimpleNamespace(environment="prod", captcha_enabled=True),
        )
        resp = await client.put(
            "/api/admin/v1/platform-config",
            json={"updates": {"captcha_enabled": ""}, "reason": "清除覆盖"},
            headers=ah,
        )
        assert resp.status_code == 200, resp.text

    async def test_clear_guard_inactive_outside_prod(self, client: AsyncClient, sm, monkeypatch):
        """非 prod 环境不受清除守卫约束。"""
        ah = await admin_headers(sm, client, role="admin")
        monkeypatch.setattr(
            "app.core.platform_config.get_settings",
            lambda: SimpleNamespace(environment="test", real_name_enabled=False),
        )
        resp = await client.put(
            "/api/admin/v1/platform-config",
            json={"updates": {"real_name_enabled": ""}, "reason": "清除覆盖"},
            headers=ah,
        )
        assert resp.status_code == 200, resp.text

    async def test_audit_records_clear_vs_set(self, client: AsyncClient, sm):
        """审计落键名 + 动作类型(clear/set),不落值。"""
        from app.core.audit import AuditLog

        ah = await admin_headers(sm, client, role="admin")
        resp = await client.put(
            "/api/admin/v1/platform-config",
            json={"updates": {"icp_number": "京ICP备2026099999号-1"}, "reason": "先写"},
            headers=ah,
        )
        assert resp.status_code == 200, resp.text
        resp = await client.put(
            "/api/admin/v1/platform-config",
            json={"updates": {"icp_number": ""}, "reason": "再清"},
            headers=ah,
        )
        assert resp.status_code == 200, resp.text
        async with sm() as session:
            rows = (
                (
                    await session.execute(
                        select(AuditLog)
                        .where(AuditLog.target == "platform_config")
                        .order_by(AuditLog.id)
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) >= 2
        assert rows[-2].detail["keys"] == {"icp_number": "set"}
        assert rows[-1].detail["keys"] == {"icp_number": "clear"}
        # 值不落审计
        assert "京ICP备" not in str(rows[-2].detail)


class TestProdComplianceGates:
    """启动合规闸 fail-fast(env/部署层)。"""

    def test_prod_refuses_boot_with_switches_off(self):
        from app.core.platform_config import assert_prod_compliance_gates

        off = {
            "captcha_enabled": "false",
            "real_name_enabled": "false",
            "real_name_required_for_recharge": "false",
        }
        with pytest.raises(RuntimeError, match="合规开关未全开"):
            assert_prod_compliance_gates(off, "prod")
        # 只开一部分同样拒
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
        """管理端写入 → GET 脱敏回读 → 公开 site-config 透出 → 空串清除覆盖回退 env 默认。"""
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
        # secret:GET 只回状态与尾 4 位预览
        secret_item = items["sms_access_key_secret"]
        assert secret_item["value"] is None
        assert secret_item["configured"] is True
        assert secret_item["preview"] == "****9876"
        assert "PLAINTEXT-SECRET-9876" not in str(data)

        # DB 落密文
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

        # 空串 = 清除覆盖,回退 env 默认
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
        """白名单外的键(如 JWT 密钥)不可写。"""
        ah = await admin_headers(sm, client, role="admin")
        resp = await client.put(
            "/api/admin/v1/platform-config",
            json={"updates": {"jwt_secret": "hack"}, "reason": "attack"},
            headers=ah,
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "VALIDATION_ERROR"

    async def test_required_real_name_needs_enabled_in_any_env(self, client: AsyncClient, sm):
        """「充值强制实名」必须伴随「实名认证已开启」,任何环境两个方向都拦;同一批一起开放行。"""
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
        # 同一批一起开:放行
        resp = await put({"real_name_required_for_recharge": "true", "real_name_enabled": "true"})
        assert resp.status_code == 200, resp.text
        # 反向:强制实名开着,再关实名认证被拒
        assert (await put({"real_name_enabled": "false"})).status_code == 400

    async def test_real_name_flag_flows_to_policies_and_gate(self, client: AsyncClient, sm):
        """开关走平台配置:公开 policies 与充值门禁即时生效。"""
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
        """渠道开关默认关:未开通渠道下单被拒;开通但凭据不全同样拒。"""
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
        """凭据经管理端录入后,工厂按生效配置构造阿里云渠道。"""
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
        """host 不带 scheme、机器人名带 robot$ 前缀、代理映射逐行 <上游>=<项目>;格式错即拒。"""
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
        """多行 text 配置逐行锚定校验:非法行被拒;超长对抗输入线性时间返回。"""
        import time

        assert validate_setting_value(
            "image_allowed_registries", "docker.io/\nregistry.example.com/team/"
        )
        with pytest.raises(ValueError, match="含非法行"):
            validate_setting_value("image_allowed_registries", "docker.io/\nBAD HOST!!")
        # 对抗输入:全部合法字符 + 一个非法尾字符
        evil = "a" * 4000 + "!"
        t0 = time.perf_counter()
        with pytest.raises(ValueError):
            validate_setting_value("image_allowed_registries", evil)
        assert time.perf_counter() - t0 < 1.0  # 线性时间
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
        # 非 admin 角色不可探测
        ops = await admin_headers(sm, client, role="ops")
        assert (
            await client.post("/api/admin/v1/platform-config/test-registry", headers=ops)
        ).status_code == 403


class TestConfigWarnings:
    def test_rules_by_switch_and_credentials(self):
        """warnings 每条规则一例。"""
        from app.core.platform_config import SETTING_SPECS, compute_config_warnings

        base = dict.fromkeys(SETTING_SPECS, "")
        base.update(
            captcha_enabled="false",
            admin_mfa_enabled="true",
            real_name_enabled="false",
            registry_host="harbor.example.com",  # Harbor 地址自动进白名单,不触发规则 6
        )
        assert compute_config_warnings(base, "test") == []
        # 镜像仓库:填了机器人未填 Secret → error;prod 无白名单且无 Harbor 地址 → warning
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
        """开启人机验证而未录凭据:warnings 带 error 级提示。"""
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
        """单行密文损坏:抛错,不回落 env。"""
        from app.core.platform_config import PlatformSetting, get_effective_platform_config

        async with sm() as session:
            session.add(
                PlatformSetting(
                    key="sms_access_key_secret",
                    value="enc:v2:000000000000:corrupt",
                    updated_by=None,
                )
            )
            await session.commit()
        async with sm() as session:
            with pytest.raises(ValueError, match="解密失败"):
                await get_effective_platform_config(session)
