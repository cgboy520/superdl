"""近似库存:市场页只展示近似值(30s 进程内缓存),创建以 K8s 调度结果为准。

数据源是节点台账(node_specs,巡检 60s 粒度)而非请求路径直连 K8s:
真实容量估算由 orchestrator 在 wire_modules() 时注册 provider;未接线即查询是装配 bug,直接报错。

缓存形态:一次批量计算覆盖全部 SKU(单次 DB 查询),按「覆盖的 sku_id 集合」命中;
provider 故障且有旧快照时回退陈旧值(stale-while-error),绝不让 /skus 因库存挂掉而 500。
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.modules.catalog.models import Sku

# (session, skus) -> {sku_id: 可售实例数};批量接口,一次调用算完全部 SKU
InventoryProvider = Callable[[AsyncSession, list["Sku"]], Awaitable[dict[int, int]]]

CACHE_TTL_SECONDS = 30.0

logger = get_logger(__name__)

_cache: tuple[float, dict[int, int]] | None = None
_lock = asyncio.Lock()
_provider: InventoryProvider | None = None


def register_inventory_provider(provider: InventoryProvider) -> None:
    global _provider
    _provider = provider


def clear_cache() -> None:
    global _cache
    _cache = None


async def get_available_counts(session: AsyncSession, skus: list["Sku"]) -> dict[int, int]:
    """全部 SKU 的近似库存。命中要求缓存覆盖请求集合(新 SKU 上架会触发一次刷新)。"""
    global _cache
    if _provider is None:
        raise RuntimeError("inventory provider 未注册:入口必须先执行 wire_modules()")
    if not skus:
        return {}
    now = time.monotonic()
    if _cache is not None:
        ts, counts = _cache
        if now - ts < CACHE_TTL_SECONDS and all(s.id in counts for s in skus):
            return {s.id: counts[s.id] for s in skus}
    async with _lock:  # single-flight:并发缓存未命中只放行一次批量计算
        if _cache is not None:
            ts, counts = _cache
            if now - ts < CACHE_TTL_SECONDS and all(s.id in counts for s in skus):
                return {s.id: counts[s.id] for s in skus}
        try:
            counts = await _provider(session, skus)
        except Exception:
            if _cache is not None:
                # stale-while-error:台账查询抽风时市场页展示旧值而不是 500
                logger.warning("inventory_refresh_failed_serving_stale")
                ts, stale = _cache
                return {s.id: stale.get(s.id, 0) for s in skus}
            raise
        _cache = (now, counts)
        return counts
