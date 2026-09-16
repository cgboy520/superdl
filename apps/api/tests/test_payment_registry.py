"""Payment channel registry: invariants, webhook mounting per environment, site-config channel
list, currency gating of channels, config warnings and the recharge presentation."""

import pytest
from fastapi.routing import APIRoute
from httpx import AsyncClient

from app.core.config import get_settings
from app.core.platform_config import runtime_config_from_strings
from app.modules.billing import payment_service
from app.modules.billing.models import Order
from app.modules.billing.payment_channels import CHANNELS, enabled_channels
from app.modules.billing.webhooks_router import build_router
from tests.helpers import create_order, set_platform_setting, user_headers


def test_registry_invariants():
    """Names fit orders.channel, webhook paths are unique, only mock is dev-only, CN wallets settle
    in CNY and mirror a payout channel."""
    assert set(CHANNELS) == {"wechat", "alipay", "mock"}
    assert all(len(name) <= 16 for name in CHANNELS)
    assert len({s.webhook_path for s in CHANNELS.values()}) == len(CHANNELS)
    assert [s.name for s in CHANNELS.values() if s.dev_only] == ["mock"]
    for name in ("wechat", "alipay"):
        assert CHANNELS[name].currencies == frozenset({"CNY"})
        assert CHANNELS[name].payout_channel == f"{name}_transfer"
        assert CHANNELS[name].presentation == "qr"
    assert CHANNELS["mock"].currencies is None and CHANNELS["mock"].payout_channel is None


def _api_routes(environment: str) -> list[APIRoute]:
    settings = get_settings().model_copy(update={"environment": environment})
    return [r for r in build_router(settings).routes if isinstance(r, APIRoute)]


def test_webhook_routes_follow_registry_and_environment():
    dev_paths = {r.path for r in _api_routes("dev")}
    prod_paths = {r.path for r in _api_routes("prod")}
    assert dev_paths == {"/webhooks/wechatpay", "/webhooks/alipay", "/webhooks/mock"}
    assert prod_paths == {"/webhooks/wechatpay", "/webhooks/alipay"}
    assert {r.name for r in _api_routes("dev")} == {
        "wechatpay_webhook",
        "alipay_webhook",
        "mock_webhook",
    }


def test_enabled_channels_follow_switches():
    cfg = runtime_config_from_strings({"payment_wechat_enabled": "true"})
    assert [s.name for s in enabled_channels(cfg, payment_mock=True)] == ["wechat", "mock"]
    assert [s.name for s in enabled_channels(cfg, payment_mock=False)] == ["wechat"]
    assert [
        s.name for s in enabled_channels(runtime_config_from_strings({}), payment_mock=False)
    ] == []


def test_payment_config_warnings_flag_currency_mismatch(monkeypatch):
    cfg = runtime_config_from_strings(
        {"payment_wechat_enabled": "true", "payment_alipay_enabled": "true"}
    )
    monkeypatch.setattr(get_settings(), "platform_currency", "USD")
    keys = sorted(w.key for w in payment_service.payment_config_warnings(cfg))
    assert keys == ["payment_alipay_enabled", "payment_wechat_enabled"]
    assert all(w.level == "error" for w in payment_service.payment_config_warnings(cfg))
    monkeypatch.setattr(get_settings(), "platform_currency", "CNY")
    assert payment_service.payment_config_warnings(cfg) == []


class TestRechargeGating:
    async def test_site_config_lists_enabled_channels_with_presentation(
        self, client: AsyncClient, sm
    ):
        await set_platform_setting(sm, "payment_wechat_enabled", "true")
        site = (await client.get("/api/v1/site-config")).json()
        assert site["payment_channels"] == [
            {"name": "wechat", "presentation": "qr"},
            {"name": "mock", "presentation": "qr"},
        ]

    async def test_channel_refused_when_platform_currency_unsupported(
        self, client: AsyncClient, sm, monkeypatch
    ):
        """wechat enabled on a USD deployment: refused before any SDK call."""
        await set_platform_setting(sm, "payment_wechat_enabled", "true")
        monkeypatch.setattr(get_settings(), "platform_currency", "USD")
        headers = await user_headers(client, "13700000401")
        resp = await client.post(
            "/api/v1/wallet/recharges",
            json={"amount": "50.00", "channel": "wechat"},
            headers=headers,
        )
        assert resp.status_code >= 400
        assert resp.json()["message_key"] == "billing.channelCurrencyUnsupported"
        ok = await create_order(client, headers, "50.00")
        assert ok["presentation"] == "qr" and ok["payment_url"]

    async def test_unknown_channel_is_validation_error(self, client: AsyncClient):
        headers = await user_headers(client, "13700000402")
        resp = await client.post(
            "/api/v1/wallet/recharges",
            json={"amount": "50.00", "channel": "paypal"},
            headers=headers,
        )
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "billing.unknownChannel"


@pytest.mark.parametrize("channel", ["wechat", "alipay", "mock"])
def test_presentation_of_known_and_legacy_channels(channel):
    assert payment_service.presentation_of(Order(channel=channel)) == "qr"
    assert payment_service.presentation_of(Order(channel="legacy")) == "qr"
