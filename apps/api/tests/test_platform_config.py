"""Platform configuration centre: allow-list validation, masked reads, overrides effective at once,
channel switch gates, role isolation."""

from types import SimpleNamespace

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import get_settings
from app.core.money import money_label
from app.core.platform_config import (
    PlatformSetting,
    runtime_config_from_strings as rc,
    validate_setting_value,
)
from app.modules.account.kyc import (
    AliyunMobile3Provider,
    KycError,
    KycSubject,
    get_kyc_provider,
)
from tests.helpers import admin_headers, create_order, pay_mock, user_headers


class TestSpecValidation:
    def test_unknown_key_and_javascript_url_rejected(self):
        """Unknown keys are refused; the licence link takes only absolute http(s) URLs (empty =
        clear the override); values are stripped before storage."""
        with pytest.raises(ValueError, match="unknown setting key"):
            validate_setting_value("jwt_secret", "x")
        with pytest.raises(ValueError):
            validate_setting_value("business_license_url", "javascript:alert(1)")
        assert (
            validate_setting_value("business_license_url", " https://example.com/l.png ")
            == "https://example.com/l.png"
        )

    @pytest.mark.parametrize("value", ["0", "00", "65536", "99999", "-25", "25.5"])
    def test_smtp_port_must_be_a_tcp_port(self, value: str):
        """An out-of-range port is refused at write time instead of failing at connect time."""
        with pytest.raises(ValueError, match="smtp_port"):
            validate_setting_value("smtp_port", value)
        assert validate_setting_value("smtp_port", "587") == "587"


SERVER_NODE_TOKEN = "K10" + "ab" * 32 + "::server:secretpassword"


class TestClusterJoinTokenShape:
    @pytest.mark.parametrize(
        "token",
        [
            "b9134731929da2d82187d52e83d7c6ab726fb0bed612005b3382f5d0e61ac300",
            "K10" + "ab" * 32 + "::node:agentpassword",
            "agent-fixture-0123456789-secrettoken",
        ],
    )
    def test_real_shapes_accepted(self, token):
        assert validate_setting_value("cluster_join_token", token) == token

    @pytest.mark.parametrize(
        "token", [SERVER_NODE_TOKEN, "K10" + "AB" * 32 + "::server:secretpassword"]
    )
    def test_server_node_token_rejected(self, token):
        """A server node-token (K10<64hex>::server:…, hex case-insensitive) would join the node as a
        server: refused."""
        with pytest.raises(ValueError, match="agent token only"):
            validate_setting_value("cluster_join_token", token)

    @pytest.mark.parametrize(
        "token",
        [
            'K10abc::server:s\nkubelet-arg:\n  - "anonymous-auth=true"',
            'K10abc::server:s" kubelet-arg: "x',
            "short",
            "K10abc::server:se cret1234567890",
        ],
    )
    def test_injection_shapes_rejected(self, token):
        with pytest.raises(ValueError, match="is malformed"):
            validate_setting_value("cluster_join_token", token)

    def test_env_layer_is_validated(self, monkeypatch):
        """Deployment-layer (env) values cannot bypass the format allow-list."""
        from app.core import platform_config

        monkeypatch.setattr(
            platform_config,
            "_env_layer",
            lambda: {"cluster_join_token": "K10abc::server:s\nkubelet-arg: x"},
        )
        problems = platform_config.env_layer_problems()
        assert problems and "cluster_join_token" in problems[0]


class TestProdDegradeForbidden:
    """Security switches (CAPTCHA / admin MFA / KYC) cannot be turned off online in prod."""

    @pytest.mark.parametrize("key", ["captcha_enabled", "admin_mfa_enabled", "real_name_enabled"])
    def test_security_switches_cannot_be_disabled_in_prod(self, key, monkeypatch):
        monkeypatch.setattr(
            "app.core.platform_config.get_settings",
            lambda: SimpleNamespace(environment="prod", compliance_profile="cn"),
        )
        with pytest.raises(ValueError, match="forbidden in production"):
            validate_setting_value(key, "false")
        assert validate_setting_value(key, "true") == "true"

    def test_cn_only_gates_relax_under_generic_profile(self, monkeypatch):
        """Under compliance_profile=none, CAPTCHA / real-name may be switched off in prod;
        admin MFA stays prod-forbidden for every profile."""
        monkeypatch.setattr(
            "app.core.platform_config.get_settings",
            lambda: SimpleNamespace(environment="prod", compliance_profile="none"),
        )
        assert validate_setting_value("captcha_enabled", "false") == "false"
        assert validate_setting_value("real_name_enabled", "false") == "false"
        with pytest.raises(ValueError, match="forbidden in production"):
            validate_setting_value("admin_mfa_enabled", "false")

    @pytest.mark.parametrize("key", ["captcha_enabled", "admin_mfa_enabled", "real_name_enabled"])
    def test_security_switches_toggle_freely_outside_prod(self, key, monkeypatch):
        monkeypatch.setattr(
            "app.core.platform_config.get_settings",
            lambda: SimpleNamespace(environment="dev"),
        )
        assert validate_setting_value(key, "false") == "false"


class TestClearOverrideFallbackGuard:
    """Clearing an override = falling back to the deployment-layer (env) value; a fallback that is
    forbidden in prod is blocked too."""

    async def test_clear_rejected_when_env_fallback_is_prod_forbidden(
        self, client: AsyncClient, sm, monkeypatch
    ):
        """Deployment-layer real_name_enabled=false (forbidden in prod) → clearing the override is
        refused."""
        ah = await admin_headers(sm, client, role="admin")
        monkeypatch.setattr(
            "app.core.platform_config.get_settings",
            lambda: SimpleNamespace(
                environment="prod", compliance_profile="cn", real_name_enabled=False
            ),
        )
        resp = await client.put(
            "/api/admin/v1/platform-config",
            json={"updates": {"real_name_enabled": ""}, "reason": "clear override"},
            headers=ah,
        )
        assert resp.status_code == 400
        assert "cannot clear the override" in resp.json()["message"]
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
        """The deployment layer is already compliant (captcha_enabled=true) → clearing passes."""
        ah = await admin_headers(sm, client, role="admin")
        monkeypatch.setattr(
            "app.core.platform_config.get_settings",
            lambda: SimpleNamespace(environment="prod", captcha_enabled=True),
        )
        resp = await client.put(
            "/api/admin/v1/platform-config",
            json={"updates": {"captcha_enabled": ""}, "reason": "clear override"},
            headers=ah,
        )
        assert resp.status_code == 200, resp.text

    async def test_clear_guard_inactive_outside_prod(self, client: AsyncClient, sm, monkeypatch):
        """Non-prod environments are not bound by the clearing guard."""
        ah = await admin_headers(sm, client, role="admin")
        monkeypatch.setattr(
            "app.core.platform_config.get_settings",
            lambda: SimpleNamespace(
                environment="test", real_name_enabled=False, real_name_required_for_recharge=False
            ),
        )
        resp = await client.put(
            "/api/admin/v1/platform-config",
            json={"updates": {"real_name_enabled": ""}, "reason": "clear override"},
            headers=ah,
        )
        assert resp.status_code == 200, resp.text

    async def test_audit_records_clear_vs_set(self, client: AsyncClient, sm):
        """The audit records key names + action type (clear/set), never values."""
        from app.core.audit import AuditLog

        ah = await admin_headers(sm, client, role="admin")
        resp = await client.put(
            "/api/admin/v1/platform-config",
            json={
                "updates": {"icp_number": "京ICP备2026099999号-1"},  # cjk-ok
                "reason": "write first",
            },  # cjk-ok
            headers=ah,
        )
        assert resp.status_code == 200, resp.text
        resp = await client.put(
            "/api/admin/v1/platform-config",
            json={"updates": {"icp_number": ""}, "reason": "then clear"},
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
        assert "京ICP备" not in str(rows[-2].detail)  # cjk-ok


class TestProdComplianceGates:
    """Boot compliance gate fail-fast (env / deployment layer)."""

    def test_prod_refuses_boot_with_switches_off(self, monkeypatch):
        from app.core.config import get_settings
        from app.core.platform_config import assert_prod_compliance_gates

        monkeypatch.setattr(get_settings(), "compliance_profile", "cn")
        off = {
            "captcha_enabled": "false",
            "real_name_enabled": "false",
            "real_name_required_for_recharge": "false",
        }
        with pytest.raises(RuntimeError, match="compliance gates are not all on"):
            assert_prod_compliance_gates(rc(off), "prod")
        with pytest.raises(RuntimeError, match="real_name_enabled"):
            assert_prod_compliance_gates(rc(dict(off, captcha_enabled="true")), "prod")

    def test_prod_boots_with_all_on_and_non_prod_unaffected(self):
        from app.core.platform_config import assert_prod_compliance_gates

        on = {
            "captcha_enabled": "true",
            "real_name_enabled": "true",
            "real_name_required_for_recharge": "true",
        }
        assert_prod_compliance_gates(rc(on), "prod")
        assert_prod_compliance_gates(rc({"captcha_enabled": "false"}), "dev")
        assert_prod_compliance_gates(rc({"captcha_enabled": "false"}), "test")

    def test_generic_profile_has_no_boot_gate(self):
        """compliance_profile=none: the CN gates do not apply, prod boots with all three off."""
        from app.core.platform_config import assert_prod_compliance_gates

        off = {
            "captcha_enabled": "false",
            "real_name_enabled": "false",
            "real_name_required_for_recharge": "false",
        }
        assert_prod_compliance_gates(rc(off), "prod")


class TestAdminApi:
    async def test_get_masks_secret_and_put_overrides(self, client: AsyncClient, sm):
        """Admin write → masked GET read-back → public site-config exposure → empty string clears
        the override back to the env default."""
        ah = await admin_headers(sm, client, role="admin")
        resp = await client.put(
            "/api/admin/v1/platform-config",
            json={
                "updates": {
                    "icp_number": "京ICP备2026012345号-1",  # cjk-ok
                    "sms_sign_name": "SuperDL",
                    "sms_access_key_secret": "PLAINTEXT-SECRET-9876",
                    "company_name": "示例云算力(北京)有限公司",  # cjk-ok
                    "company_address": "北京市海淀区示例路 1 号",  # cjk-ok
                    "company_phone": "010-12345678",
                    "business_license_url": "https://example.com/license.png",
                },
                "reason": "pre-launch configuration",
            },
            headers=ah,
        )
        assert resp.status_code == 200, resp.text

        data = (await client.get("/api/admin/v1/platform-config", headers=ah)).json()
        items = {i["key"]: i for i in data["items"]}
        assert items["icp_number"]["value"] == "京ICP备2026012345号-1"  # cjk-ok
        assert items["icp_number"]["source"] == "override"
        assert items["company_name"]["group"] == "compliance"
        secret_item = items["sms_access_key_secret"]
        assert secret_item["value"] is None
        assert secret_item["configured"] is True
        assert secret_item["preview"] == "****9876"
        assert "PLAINTEXT-SECRET-9876" not in str(data)

        async with sm() as session:
            row = (
                await session.execute(
                    select(PlatformSetting).where(PlatformSetting.key == "sms_access_key_secret")
                )
            ).scalar_one()
            assert row.value.startswith("enc:v2:")
            assert "PLAINTEXT" not in row.value

        site = (await client.get("/api/v1/site-config")).json()
        assert site["icp_number"] == "京ICP备2026012345号-1"  # cjk-ok
        assert site["company_name"] == "示例云算力(北京)有限公司"  # cjk-ok
        assert site["company_address"] == "北京市海淀区示例路 1 号"  # cjk-ok
        assert site["company_phone"] == "010-12345678"
        assert site["business_license_url"] == "https://example.com/license.png"
        assert site["payment_channels"] == [{"name": "mock", "presentation": "qr"}]

        await client.put(
            "/api/admin/v1/platform-config",
            json={
                "updates": {"icp_number": "", "business_license_url": ""},
                "reason": "clear test",
            },
            headers=ah,
        )
        site = (await client.get("/api/v1/site-config")).json()
        assert site["icp_number"] is None
        assert site["business_license_url"] is None

    async def test_unknown_key_rejected_via_api(self, client: AsyncClient, sm):
        """Keys outside the allow-list (such as the JWT secret) cannot be written."""
        ah = await admin_headers(sm, client, role="admin")
        resp = await client.put(
            "/api/admin/v1/platform-config",
            json={"updates": {"jwt_secret": "hack"}, "reason": "attack"},
            headers=ah,
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "VALIDATION_ERROR"

    async def test_required_real_name_needs_enabled_in_any_env(self, client: AsyncClient, sm):
        """ "KYC required for top-ups" needs "KYC enabled"; both directions are blocked in every
        environment; opening both in one batch passes."""
        ah = await admin_headers(sm, client, role="admin")

        async def put(updates: dict[str, str]):
            return await client.put(
                "/api/admin/v1/platform-config",
                json={"updates": updates, "reason": "compliance enable"},
                headers=ah,
            )

        resp = await put({"real_name_required_for_recharge": "true"})
        assert resp.status_code == 400, resp.text
        assert resp.json()["code"] == "VALIDATION_ERROR"
        assert "real_name_enabled" in resp.json()["message"]
        resp = await put({"real_name_required_for_recharge": "true", "real_name_enabled": "true"})
        assert resp.status_code == 200, resp.text
        assert (await put({"real_name_enabled": "false"})).status_code == 400

    async def test_real_name_flag_flows_to_policies_and_gate(
        self, client: AsyncClient, sm, monkeypatch
    ):
        """The switches live in the platform configuration: public policies and the top-up gate
        follow at once (the gate applies only to profiles with a KYC form)."""
        ah = await admin_headers(sm, client, role="admin")
        base = (await client.get("/api/v1/policies")).json()
        assert base["real_name_enabled"] is False
        assert base["real_name_required_for_recharge"] is False

        resp = await client.put(
            "/api/admin/v1/platform-config",
            json={
                "updates": {"real_name_enabled": "true", "real_name_required_for_recharge": "true"},
                "reason": "compliance enable",
            },
            headers=ah,
        )
        assert resp.status_code == 200, resp.text
        policies = (await client.get("/api/v1/policies")).json()
        assert policies["real_name_enabled"] is True
        assert policies["real_name_required_for_recharge"] is True

        headers = await user_headers(client, "u13700000201@test.local")
        monkeypatch.setattr(get_settings(), "compliance_profile", "cn")
        resp = await client.post(
            "/api/v1/wallet/recharges",
            json={"amount": "50.00", "channel": "mock"},
            headers=headers,
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "REAL_NAME_REQUIRED"


class TestChannelGate:
    async def test_disabled_channel_rejected(self, client: AsyncClient, sm, monkeypatch):
        """Channel switches default off: an order on a disabled channel is refused; enabled with
        incomplete credentials is refused too (the credential check runs only when the currency
        matches)."""
        monkeypatch.setattr(get_settings(), "platform_currency", "CNY")
        headers = await user_headers(client, "u13700000202@test.local")
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
            json={"updates": {"payment_wechat_enabled": "true"}, "reason": "integration test"},
            headers=ah,
        )
        resp = await client.post(
            "/api/v1/wallet/recharges",
            json={"amount": "50.00", "channel": "wechat"},
            headers=headers,
        )
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "billing.wechatCredentialsIncomplete"


_SUBJECT = KycSubject(
    user_id=1,
    full_name="张三",  # cjk-ok
    identity_number="110101199001011237",
    phone="+8613800000000",
    email=None,
    country="CN",
)


class TestAliyunMobile3Provider:
    def _provider(self, handler) -> AliyunMobile3Provider:
        return AliyunMobile3Provider("ak", "sk", transport=httpx.MockTransport(handler))

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
        first = await provider.verify(_SUBJECT)
        assert first.verified and first.provider == "aliyun_mobile3"
        assert first.identity_key == "110101199001011237"
        assert (await provider.verify(_SUBJECT)).verified is False
        assert (await provider.verify(_SUBJECT)).verified is False
        with pytest.raises(KycError, match="403"):
            await provider.verify(_SUBJECT)

    async def test_national_phone_sent_and_region_check(self):
        seen: list[dict[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(dict(pair.split("=", 1) for pair in request.content.decode().split("&")))
            return httpx.Response(200, json={"Code": "200", "ResultObject": {"BizCode": "1"}})

        await self._provider(handler).verify(_SUBJECT)
        assert seen[0]["Mobile"] == "13800000000"
        from dataclasses import replace

        from app.modules.account.kyc import KycRegionUnsupported

        with pytest.raises(KycRegionUnsupported):
            await self._provider(handler).verify(replace(_SUBJECT, phone="+14155550123"))
        with pytest.raises(KycRegionUnsupported):
            await self._provider(handler).verify(replace(_SUBJECT, phone=None))

    async def test_factory_builds_aliyun_from_config(self, client: AsyncClient, sm):
        """Credentials entered through the admin API: the factory builds the Aliyun channel from the
        effective configuration."""
        ah = await admin_headers(sm, client, role="admin")
        await client.put(
            "/api/admin/v1/platform-config",
            json={
                "updates": {
                    "real_name_access_key_id": "LTAI5tTESTTESTTEST",
                    "real_name_access_key_secret": "sk-test-secret",
                },
                "reason": "connecting Aliyun KYC",
            },
            headers=ah,
        )
        async with sm() as session:
            provider = await get_kyc_provider(session)
        assert isinstance(provider, AliyunMobile3Provider)


class TestRegistrySpecsAndProbeEndpoint:
    def test_registry_key_validation(self):
        """host without scheme, robot name with the robot$ prefix, proxy mapping
        <upstream>=<project>
        per line; malformed values are refused."""
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
        """Multi-line text settings are validated per anchored line: invalid lines are refused;
        over-long adversarial input returns in linear time."""
        import time

        assert validate_setting_value(
            "image_allowed_registries", "docker.io/\nregistry.example.com/team/"
        )
        with pytest.raises(ValueError, match="has an invalid line"):
            validate_setting_value("image_allowed_registries", "docker.io/\nBAD HOST!!")
        evil = "a" * 4000 + "!"
        t0 = time.perf_counter()
        with pytest.raises(ValueError):
            validate_setting_value("image_allowed_registries", evil)
        assert time.perf_counter() - t0 < 1.0
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
        assert resp.status_code == 400

        async def fake_probe(**kwargs):
            assert kwargs["host"] == "harbor.example.com" and kwargs["project"] == "superdl"
            return HarborProbe(True, "done", "ok", "v2.12.0", 3)

        monkeypatch.setattr(router_ops, "probe_harbor", fake_probe)
        await client.put(
            "/api/admin/v1/platform-config",
            json={
                "updates": {"registry_host": "harbor.example.com"},
                "reason": "connecting Harbor",
            },
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
        ops = await admin_headers(sm, client, role="ops")
        assert (
            await client.post("/api/admin/v1/platform-config/test-registry", headers=ops)
        ).status_code == 403


class TestConfigWarnings:
    def test_rules_by_switch_and_credentials(self):
        """Prod-forbidden values derive warnings from SettingSpec; compliance gates are error, the
        rest warning."""
        from app.core.platform_config import SETTING_SPECS, compute_config_warnings

        base = dict.fromkeys(SETTING_SPECS, "")
        base.update(
            captcha_enabled="false",
            admin_mfa_enabled="true",
            real_name_enabled="false",
            real_name_required_for_recharge="false",
            registry_host="harbor.example.com",
        )
        assert compute_config_warnings(rc(base), "test") == []
        robot_only = dict(base, registry_robot_name="robot$superdl+pull")
        assert [(w.key, w.level) for w in compute_config_warnings(rc(robot_only), "test")] == [
            ("registry_robot_name", "error")
        ]
        compliant = dict(
            base,
            captcha_enabled="true",
            real_name_enabled="true",
            real_name_required_for_recharge="true",
        )
        no_registry = dict(
            compliant,
            registry_host="",
            captcha_provider="aliyun",
            captcha_scene_id="s",
            captcha_prefix="pfx",
            captcha_access_key_id="LTAI5tTESTTESTTEST",
            captcha_access_key_secret="k",
            real_name_access_key_id="LTAI5tTESTTESTTEST",
            real_name_access_key_secret="k",
        )
        assert [(w.key, w.level) for w in compute_config_warnings(rc(no_registry), "prod")] == [
            ("image_allowed_registries", "error")
        ]
        no_prefix = dict(no_registry, captcha_prefix="")
        assert ("captcha_enabled", "error") in [
            (w.key, w.level) for w in compute_config_warnings(rc(no_prefix), "prod")
        ]
        assert [(w.key, w.level) for w in compute_config_warnings(rc(base), "prod")] == [
            ("captcha_enabled", "warning"),
        ]
        on = dict(compliant, admin_mfa_enabled="false", sms_provider="mock")
        keys = {(w.key, w.level) for w in compute_config_warnings(rc(on), "prod")}
        assert keys == {
            ("captcha_enabled", "error"),
            ("admin_mfa_enabled", "warning"),
            ("real_name_enabled", "error"),
            ("sms_provider", "warning"),
        }
        on["sms_provider"] = ""
        full = dict(
            on,
            captcha_provider="aliyun",
            captcha_scene_id="scene",
            captcha_prefix="pfx",
            captcha_access_key_id="LTAI5tTESTTESTTEST",
            captcha_access_key_secret="sk",
            real_name_access_key_id="LTAI5tTESTTESTTEST",
            real_name_access_key_secret="sk",
        )
        assert [w.key for w in compute_config_warnings(rc(full), "prod")] == ["admin_mfa_enabled"]
        assert compute_config_warnings(rc(dict(full, admin_mfa_enabled="true")), "prod") == []

    def test_provider_credential_rules(self):
        """Twilio / SMTP selected without credentials → error red card on the provider key."""
        from app.core.platform_config import SETTING_SPECS, compute_config_warnings

        base = dict.fromkeys(SETTING_SPECS, "")
        base.update(
            registry_host="harbor.example.com", sms_provider="twilio", email_provider="smtp"
        )
        assert [(w.key, w.level) for w in compute_config_warnings(rc(base), "test")] == [
            ("sms_provider", "error"),
            ("email_provider", "error"),
        ]
        full = dict(
            base,
            sms_twilio_account_sid="AC" + "0" * 32,
            sms_twilio_auth_token="t",
            sms_twilio_from="+14155550123",
            smtp_host="smtp.example.com",
            email_from="no-reply@example.com",
        )
        assert compute_config_warnings(rc(full), "test") == []
        turnstile_on = dict(base, captcha_enabled="true", captcha_provider="turnstile")
        assert ("captcha_enabled", "error") in {
            (w.key, w.level) for w in compute_config_warnings(rc(turnstile_on), "test")
        }
        turnstile_ok = dict(
            turnstile_on,
            captcha_turnstile_site_key="0x4AAAAAAA_site",
            captcha_turnstile_secret_key="sec",
        )
        assert "captcha_enabled" not in {
            w.key for w in compute_config_warnings(rc(turnstile_ok), "test")
        }

    def test_cn_profile_turns_gates_into_errors(self, monkeypatch):
        """Under compliance_profile=cn the three CN gates are prod errors (boot-blocking)."""
        from app.core.config import get_settings
        from app.core.platform_config import SETTING_SPECS, compute_config_warnings

        monkeypatch.setattr(get_settings(), "compliance_profile", "cn")
        base = dict.fromkeys(SETTING_SPECS, "")
        base.update(
            captcha_enabled="false",
            admin_mfa_enabled="true",
            real_name_enabled="false",
            real_name_required_for_recharge="false",
            registry_host="harbor.example.com",
        )
        assert [(w.key, w.level) for w in compute_config_warnings(rc(base), "prod")] == [
            ("captcha_enabled", "error"),
            ("real_name_enabled", "error"),
            ("real_name_required_for_recharge", "error"),
        ]

    async def test_api_exposes_warnings(self, client: AsyncClient, sm):
        """CAPTCHA on without credentials: warnings carry an error-level hint."""
        ah = await admin_headers(sm, client, role="admin")
        assert (await client.get("/api/admin/v1/platform-config", headers=ah)).json()[
            "warnings"
        ] == []
        resp = await client.put(
            "/api/admin/v1/platform-config",
            json={"updates": {"captcha_enabled": "true"}, "reason": "switch on first"},
            headers=ah,
        )
        assert resp.status_code == 200, resp.text
        warnings = (await client.get("/api/admin/v1/platform-config", headers=ah)).json()[
            "warnings"
        ]
        assert [(w["key"], w["level"]) for w in warnings] == [("captcha_enabled", "error")]


class TestEffectiveConfig:
    async def test_corrupt_secret_row_fails_closed(self, sm):
        """A corrupt single-row ciphertext: raises, no fallback to env."""
        from app.core.platform_config import PlatformSetting, get_runtime_config

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
            with pytest.raises(ValueError, match="decryption failed"):
                await get_runtime_config(session)


class TestProdImageAllowlistGate:
    def test_empty_allowlist_refuses_prod_start(self):
        from app.core.platform_config import assert_prod_image_allowlist

        with pytest.raises(RuntimeError, match="image source allow-list"):
            assert_prod_image_allowlist(
                rc({"registry_host": "", "image_allowed_registries": ""}), "prod"
            )
        assert_prod_image_allowlist(
            rc({"registry_host": "harbor.example.com", "image_allowed_registries": ""}), "prod"
        )
        assert_prod_image_allowlist(
            rc({"registry_host": "", "image_allowed_registries": ""}), "dev"
        )


class TestSpecsMatchSettingsAndRuntimeConfig:
    def test_every_key_has_env_default_with_matching_type(self):
        from decimal import Decimal

        from app.core.config import Settings
        from app.core.platform_config import RUNTIME_CONFIG_FIELDS, SETTING_SPECS, RuntimeConfig

        assert set(RUNTIME_CONFIG_FIELDS) == set(SETTING_SPECS)
        assert set(SETTING_SPECS) <= set(Settings.model_fields)
        expected = {"bool": bool, "int": int, "decimal": Decimal}
        hints = RuntimeConfig.__dataclass_fields__
        for key, spec in SETTING_SPECS.items():
            assert hints[key].type is expected.get(spec.kind, str), key
            if spec.kind in ("int", "decimal"):
                assert spec.lo is not None and spec.hi is not None, key
                assert spec.group == "policy", key

    def test_env_defaults_round_trip(self):
        """Every env default passes its own allow-list validation and converts to the target
        type."""
        from app.core.platform_config import (
            RuntimeConfig,
            env_layer_problems,
            runtime_config_from_strings,
        )

        assert env_layer_problems() == []
        assert isinstance(runtime_config_from_strings({}), RuntimeConfig)


class TestPolicyOverrides:
    """Policy parameters (policy group): /policies endpoints, public output, out-of-range refusal,
    disk price snapshot, group isolation."""

    async def test_default_then_override_flows_to_public_endpoint(self, client: AsyncClient, sm):
        base = (await client.get("/api/v1/policies")).json()
        assert base["disk_price_gb_month"] == "0.0350"

        ah = await admin_headers(sm, client, role="admin")
        resp = await client.put(
            "/api/admin/v1/policies",
            json={"updates": {"disk_price_gb_month": "0.0500"}, "reason": "quarterly repricing"},
            headers=ah,
        )
        assert resp.status_code == 200, resp.text

        updated = (await client.get("/api/v1/policies")).json()
        assert updated["disk_price_gb_month"] == "0.0500"

        admin_view = (await client.get("/api/admin/v1/policies", headers=ah)).json()
        assert admin_view["overrides"]["disk_price_gb_month"] == "0.0500"
        assert admin_view["effective"]["disk_price_gb_month"] == "0.0500"
        assert "specs" in admin_view

    async def test_new_disk_snapshots_overridden_price(self, client: AsyncClient, sm):
        """The disk price is snapshotted at creation: new disks after the override use the new
        price."""
        ah = await admin_headers(sm, client, role="admin")
        await client.put(
            "/api/admin/v1/policies",
            json={"updates": {"disk_price_gb_month": "0.0700"}, "reason": "test repricing"},
            headers=ah,
        )
        headers = await user_headers(client, "u13700000031@test.local")
        order = await create_order(client, headers, "100.00")
        await pay_mock(client, order["order_no"], "100.00")
        resp = await client.post(
            "/api/v1/disks", json={"name": "d1", "size_gb": 50}, headers=headers
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["price_gb_month"] == "0.0700"

    async def test_platform_config_endpoint_rejects_policy_keys(self, client: AsyncClient, sm):
        """The two endpoint groups are isolated by setting group: /platform-config takes no policy
        keys, /policies no keys of other groups."""
        ah = await admin_headers(sm, client, role="admin")
        resp = await client.put(
            "/api/admin/v1/platform-config",
            json={"updates": {"disk_min_gb": "20"}, "reason": "wrong door"},
            headers=ah,
        )
        assert resp.status_code == 400
        resp = await client.put(
            "/api/admin/v1/policies",
            json={"updates": {"icp_number": "x"}, "reason": "wrong door"},
            headers=ah,
        )
        assert resp.status_code == 400
        items = (await client.get("/api/admin/v1/platform-config", headers=ah)).json()["items"]
        assert all(i["group"] != "policy" for i in items)

    async def test_recharge_bounds_and_presets_are_policies(self, client: AsyncClient, sm):
        """/policies publishes recharge_min / recharge_max / recharge_presets; raising the minimum
        refuses smaller top-ups; min > max and presets outside the bounds are rejected."""
        base = (await client.get("/api/v1/policies")).json()
        assert base["recharge_min"] == "1.00" and base["recharge_max"] == "50000.00"
        assert base["recharge_presets"] == ["50.00", "100.00", "500.00"]
        ah = await admin_headers(sm, client, role="admin")
        resp = await client.put(
            "/api/admin/v1/policies",
            json={
                "updates": {"recharge_min": "20", "recharge_presets": "20,200"},
                "reason": "raise the floor",
            },
            headers=ah,
        )
        assert resp.status_code == 200, resp.text
        policies = (await client.get("/api/v1/policies")).json()
        assert policies["recharge_min"] == "20.00"
        assert policies["recharge_presets"] == ["20.00", "200.00"]
        headers = await user_headers(client, "u13700000033@test.local")
        low = await client.post(
            "/api/v1/wallet/recharges", json={"amount": "10.00", "channel": "mock"}, headers=headers
        )
        assert low.status_code == 400
        assert low.json()["message_key"] == "billing.rechargeAmountOutOfRange"
        assert low.json()["params"]["min"] == money_label("20")
        ok = await client.post(
            "/api/v1/wallet/recharges", json={"amount": "20.00", "channel": "mock"}, headers=headers
        )
        assert ok.status_code == 201, ok.text
        for bad in (
            {"recharge_min": "60000"},
            {"recharge_presets": "5"},
            {"recharge_presets": "20;200"},
        ):
            resp = await client.put(
                "/api/admin/v1/policies", json={"updates": bad, "reason": "bad"}, headers=ah
            )
            assert resp.status_code == 400, (bad, resp.text)
        admin_view = (await client.get("/api/admin/v1/policies", headers=ah)).json()
        assert admin_view["specs"]["recharge_presets"] == {"kind": "str", "min": None, "max": None}

    async def test_invalid_updates_rejected(self, client: AsyncClient, sm):
        ah = await admin_headers(sm, client, role="admin")
        resp = await client.put(
            "/api/admin/v1/policies",
            json={"updates": {"disk_price_gb_month": "9.99"}, "reason": "slip"},
            headers=ah,
        )
        assert resp.status_code == 400
        resp = await client.put(
            "/api/admin/v1/policies",
            json={"updates": {"jwt_secret": "hack"}, "reason": "privilege escalation"},
            headers=ah,
        )
        assert resp.status_code == 400
