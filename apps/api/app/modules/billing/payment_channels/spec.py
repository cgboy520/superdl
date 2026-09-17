"""Payment channel registry: one `ChannelSpec` per channel drives the enable check, webhook route,
currency support, refund payout counterpart and how the console presents the payment (QR vs
redirect). Adding a channel = one entry here plus its implementation module. Registered:
`wechat` / `alipay` (CNY, QR), `stripe` (any platform currency, hosted checkout) and the
dev-only `mock`."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from fastapi.responses import JSONResponse, PlainTextResponse, Response

from app.core.platform_config import RuntimeConfig
from app.modules.billing.payment_channels.alipay import ALIPAY_CFG_KEYS, AlipayChannel
from app.modules.billing.payment_channels.base import PaymentChannel
from app.modules.billing.payment_channels.mock import MockChannel
from app.modules.billing.payment_channels.stripe import STRIPE_CFG_KEYS, StripeChannel
from app.modules.billing.payment_channels.wechat import WECHAT_CFG_KEYS, WechatChannel

Presentation = Literal["qr", "redirect"]

CHANNEL_NAME_MAX = 16  # orders.channel VARCHAR(16)


@dataclass(frozen=True)
class ChannelSpec:
    name: str
    presentation: Presentation
    #: RuntimeConfig bool that switches the channel on; None for env-driven (dev-only) channels.
    enabled_key: str | None
    #: RuntimeConfig keys whose values fingerprint a constructed channel instance.
    config_keys: tuple[str, ...]
    #: Builds the channel (runs in the SDK thread pool; may raise a channel error).
    factory: Callable[[RuntimeConfig], PaymentChannel]
    #: `POST /api/v1/webhooks/{webhook_path}`.
    webhook_path: str
    #: Response body the channel expects after a callback, given the handling status.
    ack: Callable[[str], Response]
    #: ISO 4217 codes the channel settles in; None = any platform currency.
    currencies: frozenset[str] | None
    #: Refund payout channel that mirrors this channel (None = offline only).
    payout_channel: str | None
    dev_only: bool = False
    supports_query: bool = True

    def enabled(self, cfg: RuntimeConfig, *, payment_mock: bool) -> bool:
        if self.dev_only:
            return payment_mock
        return bool(getattr(cfg, self.enabled_key)) if self.enabled_key else True


def _json_ack(body: Callable[[str], dict[str, str]]) -> Callable[[str], Response]:
    return lambda status: JSONResponse(body(status))


CHANNELS: dict[str, ChannelSpec] = {
    spec.name: spec
    for spec in (
        ChannelSpec(
            name="wechat",
            presentation="qr",
            enabled_key="payment_wechat_enabled",
            config_keys=WECHAT_CFG_KEYS,
            factory=WechatChannel,
            webhook_path="wechatpay",
            ack=_json_ack(lambda status: {"code": "SUCCESS", "message": status}),
            currencies=frozenset({"CNY"}),
            payout_channel="wechat_transfer",
        ),
        ChannelSpec(
            name="alipay",
            presentation="qr",
            enabled_key="payment_alipay_enabled",
            config_keys=ALIPAY_CFG_KEYS,
            factory=AlipayChannel,
            webhook_path="alipay",
            ack=lambda _status: PlainTextResponse("success"),
            currencies=frozenset({"CNY"}),
            payout_channel="alipay_transfer",
        ),
        ChannelSpec(
            name="stripe",
            presentation="redirect",
            enabled_key="payment_stripe_enabled",
            config_keys=STRIPE_CFG_KEYS,
            factory=StripeChannel,
            webhook_path="stripe",
            ack=_json_ack(lambda status: {"received": status}),
            currencies=None,
            payout_channel=None,
        ),
        ChannelSpec(
            name="mock",
            presentation="qr",
            enabled_key=None,
            config_keys=(),
            factory=lambda _cfg: MockChannel(),
            webhook_path="mock",
            ack=_json_ack(lambda status: {"status": status}),
            currencies=None,
            payout_channel=None,
            dev_only=True,
        ),
    )
}

if any(len(name) > CHANNEL_NAME_MAX for name in CHANNELS):
    raise RuntimeError("payment channel name exceeds orders.channel width")
if len({spec.webhook_path for spec in CHANNELS.values()}) != len(CHANNELS):
    raise RuntimeError("payment channel webhook paths must be unique")


def enabled_channels(cfg: RuntimeConfig, *, payment_mock: bool) -> list[ChannelSpec]:
    """Channels a user may pick right now, in registry order."""
    return [spec for spec in CHANNELS.values() if spec.enabled(cfg, payment_mock=payment_mock)]
