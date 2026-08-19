"""近似库存:市场页只展示近似值(30s 进程内缓存),创建以 K8s 调度结果为准。

真实容量估算由 orchestrator 在启动时注册 provider(WP3);此前用 stub。
"""

import time
from collections.abc import Awaitable, Callable
from typing import Any

InventoryProvider = Callable[[Any], Awaitable[int]]  # (sku) -> 可租卡数

CACHE_TTL_SECONDS = 30.0

_cache: dict[int, tuple[int, float]] = {}


async def _stub_provider(_sku: Any) -> int:
    """WP3 前的占位:固定可租数,让市场页先跑通。"""
    return 8


_provider: InventoryProvider = _stub_provider


def register_inventory_provider(provider: InventoryProvider) -> None:
    global _provider
    _provider = provider


def clear_cache() -> None:
    _cache.clear()


async def get_available_count(sku: Any) -> int:
    now = time.monotonic()
    hit = _cache.get(sku.id)
    if hit is not None and now - hit[1] < CACHE_TTL_SECONDS:
        return hit[0]
    count = await _provider(sku)
    _cache[sku.id] = (count, now)
    return count
