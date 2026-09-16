"""Compliance profiles: resolution from settings, what each profile switches on, and how the
deployment identity is published on /site-config and the admin platform-config page."""

from httpx import AsyncClient

from app.core.compliance import PROFILES, current_profile, profile_for
from app.core.config import get_settings
from tests.helpers import admin_headers


class TestProfiles:
    def test_unset_resolves_to_generic(self):
        assert profile_for(None) is PROFILES["none"]
        assert profile_for("cn") is PROFILES["cn"]

    def test_generic_profile_has_no_regional_requirements(self):
        p = PROFILES["none"]
        assert (p.phone_required, p.phone_dial_codes, p.kyc_form, p.invoice_tax_id_rule) == (
            False,
            (),
            None,
            None,
        )
        assert p.default_locale == "en-US"

    def test_cn_profile_requires_prc_phone_and_id_card(self):
        p = PROFILES["cn"]
        assert p.phone_required and p.phone_dial_codes == ("86",)
        assert p.kyc_form == "cn_id_card" and p.invoice_tax_id_rule == "cn_uscc"
        assert p.default_locale == "zh-CN"

    def test_current_profile_follows_settings(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "compliance_profile", "cn")
        assert current_profile().name == "cn"
        monkeypatch.setattr(get_settings(), "compliance_profile", None)
        assert current_profile().name == "none"


class TestSiteConfig:
    async def test_site_config_publishes_deployment_identity(self, client: AsyncClient, sm):
        site = (await client.get("/api/v1/site-config")).json()
        assert site["compliance_profile"] == "none"
        assert site["phone_required"] is False and site["phone_dial_codes"] == []
        assert site["kyc_form"] is None and site["default_locale"] == "en-US"
        assert site["currency"] == get_settings().platform_currency
        assert site["billing_timezone"] == get_settings().billing_timezone

    async def test_site_config_under_cn_profile(self, client: AsyncClient, sm, monkeypatch):
        monkeypatch.setattr(get_settings(), "compliance_profile", "cn")
        site = (await client.get("/api/v1/site-config")).json()
        assert site["compliance_profile"] == "cn"
        assert site["phone_required"] is True and site["phone_dial_codes"] == ["86"]
        assert site["kyc_form"] == "cn_id_card" and site["default_locale"] == "zh-CN"


class TestAdminPlatformConfig:
    async def test_deployment_block_is_read_only_identity(self, client: AsyncClient, sm):
        headers = await admin_headers(sm, client)
        body = (await client.get("/api/admin/v1/platform-config", headers=headers)).json()
        settings = get_settings()
        assert body["deployment"] == {
            "compliance_profile": "none",
            "currency": settings.platform_currency,
            "billing_timezone": settings.billing_timezone,
        }
        assert not any(i["key"] == "compliance_profile" for i in body["items"])
