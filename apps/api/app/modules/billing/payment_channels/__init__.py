"""Payment channels: `mock` (dev only), `wechat`, `alipay`. `get_channel` builds real channels
through the SDK thread pool and caches them by credential fingerprint.
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
    QueryResult,
    assert_callback_fresh,
    channel_error,
    header_value,
    run_in_sdk_pool,
)
from app.modules.billing.payment_channels.mock import MockChannel
from app.modules.billing.payment_channels.wechat import WECHAT_CFG_KEYS, WechatChannel

__all__ = [
    "ALIPAY_CFG_KEYS",
    "CALLBACK_FRESHNESS_SECONDS",
    "SDK_TIMEOUT",
    "SDK_TIMEOUT_SECONDS",
    "WECHAT_CFG_KEYS",
    "AlipayChannel",
    "CallbackResult",
    "MockChannel",
    "PaymentChannel",
    "QueryResult",
    "WechatChannel",
    "assert_callback_fresh",
    "channel_error",
    "get_channel",
    "header_value",
    "run_in_sdk_pool",
]

_real_channel_cache: dict[str, tuple[tuple[str, ...], PaymentChannel]] = {}


async def get_channel(name: str, session: AsyncSession) -> PaymentChannel:
    """按配置指纹缓存真实渠道;mock 仅在 payment_mock 开启时可用。"""
    settings = get_settings()
    if name == "mock":
        if not settings.payment_mock:
            raise channel_error("billing.mockDevOnly")
        return MockChannel()
    if name not in ("wechat", "alipay"):
        raise AppError(
            ErrorCode.VALIDATION_ERROR, key="billing.unknownChannel", params={"name": name}
        )
    cfg = await get_runtime_config(session)
    keys = WECHAT_CFG_KEYS if name == "wechat" else ALIPAY_CFG_KEYS
    fingerprint = tuple(getattr(cfg, k) for k in keys)
    cached = _real_channel_cache.get(name)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]
    channel: PaymentChannel = await run_in_sdk_pool(
        WechatChannel if name == "wechat" else AlipayChannel, cfg
    )
    _real_channel_cache[name] = (fingerprint, channel)
    return channel
