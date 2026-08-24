"""近似库存:市场页只展示近似值(进程内签名缓存),创建以 K8s 调度结果为准。

数据源是节点台账(node_specs,巡检 60s 粒度)而非请求路径直连 K8s:
真实容量估算由 orchestrator 在 wire_modules() 时注册 provider;未接线即查询是装配 bug,直接报错。

缓存形态(与 platform_config 同一失效模式):一次批量计算覆盖全部 SKU(单次 DB 查询),
按「数据源签名 (台账行数, max(updated_at)) + 覆盖的 sku_id 集合」命中,签名变即重算;
provider 故障且有旧快照时回退陈旧值(stale-while-error);
冷缓存(无快照)时 provider 故障则原样上抛 —— 装配/依赖故障不可静默成「全线无货」。
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.modules.catalog.models import Sku

# (session, skus) -> {sku_id: 可售实例数};批量接口,一次调用算完全部 SKU
InventoryProvider = Callable[[AsyncSession, list["Sku"]], Awaitable[dict[int, int]]]
# (session) -> 数据源失效签名,与 compute 同源(台账行数, max(updated_at));
# 签名查询必须廉价(每次调用都执行),重计算只在签名变化时发生
SignatureProvider = Callable[[AsyncSession], Awaitable[tuple[object, ...]]]

logger = get_logger(__name__)

_cache: tuple[tuple[object, ...], dict[int, int]] | None = None
_lock = asyncio.Lock()
_provider: InventoryProvider | None = None
_signature_provider: SignatureProvider | None = None


def register_inventory_provider(
    provider: InventoryProvider, signature_provider: SignatureProvider
) -> None:
    global _provider, _signature_provider
    _provider = provider
    _signature_provider = signature_provider


def clear_cache() -> None:
    global _cache
    _cache = None


async def get_available_counts(session: AsyncSession, skus: list["Sku"]) -> dict[int, int]:
    """全部 SKU 的近似库存。签名(数据源行数/max(updated_at))未变且覆盖请求集合即命中;
    SKU 上架(集合外 id)或台账巡检写入都会触发一次重算。"""
    global _cache
    if _provider is None or _signature_provider is None:
        raise RuntimeError("inventory provider 未注册:入口必须先执行 wire_modules()")
    if not skus:
        return {}
    signature = await _signature_provider(session)
    if _cache is not None:
        sig, counts = _cache
        if sig == signature and all(s.id in counts for s in skus):
            return {s.id: counts[s.id] for s in skus}
    async with _lock:  # single-flight:并发缓存未命中只放行一次批量计算
        if _cache is not None:
            sig, counts = _cache
            if sig == signature and all(s.id in counts for s in skus):
                return {s.id: counts[s.id] for s in skus}
        try:
            counts = await _provider(session, skus)
        except Exception:
            if _cache is not None:
                # stale-while-error:台账查询抽风时市场页展示旧值而不是 500
                logger.warning("inventory_refresh_failed_serving_stale")
                _, stale = _cache
                return {s.id: stale.get(s.id, 0) for s in skus}
            raise
        # 计算完成后重取签名落缓存(与 platform_config「以快照自重算签名」同款自洽:
        # 计算期间被巡检提交的变更会让下次调用再重算,方向安全)
        built_signature = await _signature_provider(session)
        _cache = (built_signature, counts)
        return counts
