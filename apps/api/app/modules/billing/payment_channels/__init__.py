"""Payment channels (`wechat`, `alipay`, `stripe`, dev-only `mock`) behind one protocol
(`base.PaymentChannel`) and one registry (`spec.CHANNELS`).
`get_channel` builds real channels through the SDK thread pool and caches them by credential
fingerprint; the dev-only `mock` channel needs `payment_mock=true`.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.core.platform_config import get_runtime_config
from app.modules.billing.payment_channels.alipay import ALIPAY_CFG_KEYS, AlipayChannel
from app.modules.billing.payment_channels.base import (
    CALLBACK_FRESHNESS_SECONDS,
    SDK_TIMEOUT,
    SDK_TIMEOUT_SECONDS,
    CallbackResult,
    PaymentChannel,
    PaymentInit,
    QueryResult,
    assert_callback_fresh,
    channel_error,
    header_value,
    run_in_sdk_pool,
)
from app.modules.billing.payment_channels.mock import MockChannel
from app.modules.billing.payment_channels.spec import (
    CHANNELS,
    ChannelSpec,
    Presentation,
    enabled_channels,
)
from app.modules.billing.payment_channels.stripe import STRIPE_CFG_KEYS, StripeChannel
from app.modules.billing.payment_channels.wechat import WECHAT_CFG_KEYS, WechatChannel

__all__ = [
    "ALIPAY_CFG_KEYS",
    "CALLBACK_FRESHNESS_SECONDS",
    "CHANNELS",
    "SDK_TIMEOUT",
    "SDK_TIMEOUT_SECONDS",
    "STRIPE_CFG_KEYS",
    "WECHAT_CFG_KEYS",
    "AlipayChannel",
    "CallbackResult",
    "ChannelSpec",
    "MockChannel",
    "PaymentChannel",
    "PaymentInit",
    "Presentation",
    "QueryResult",
    "StripeChannel",
    "WechatChannel",
    "assert_callback_fresh",
    "channel_error",
    "enabled_channels",
    "get_channel",
    "header_value",
    "run_in_sdk_pool",
]

_real_channel_cache: dict[str, tuple[tuple[str, ...], PaymentChannel]] = {}


def channel_spec(name: str) -> ChannelSpec:
    """Registry lookup; unknown names are a validation error."""
    spec = CHANNELS.get(name)
    if spec is None:
        raise AppError(
            ErrorCode.VALIDATION_ERROR, key="billing.unknownChannel", params={"name": name}
        )
    return spec


async def get_channel(name: str, session: AsyncSession) -> PaymentChannel:
    """Channel instance for `name`: dev-only channels need `payment_mock`; real channels are built
    from the runtime config and cached until any of their `config_keys` changes."""
    spec = channel_spec(name)
    if spec.dev_only:
        if not get_settings().payment_mock:
            raise channel_error("billing.mockDevOnly")
        return MockChannel()
    cfg = await get_runtime_config(session)
    fingerprint = tuple(getattr(cfg, k) for k in spec.config_keys)
    cached = _real_channel_cache.get(name)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]
    channel: PaymentChannel = await run_in_sdk_pool(spec.factory, cfg)
    _real_channel_cache[name] = (fingerprint, channel)
    return channel
