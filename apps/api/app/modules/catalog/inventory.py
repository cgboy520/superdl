"""近似库存:市场页可售实例数,每请求按节点台账直接算,不配缓存。
估算 provider 由 orchestrator 在 wire_modules() 注入;未接线即报错。
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.modules.catalog.models import Sku

# (session, skus) -> {sku_id: 可售实例数},批量接口
InventoryProvider = Callable[[AsyncSession, list["Sku"]], Awaitable[dict[int, int]]]

_provider: InventoryProvider | None = None


def register_inventory_provider(provider: InventoryProvider) -> None:
    global _provider
    _provider = provider


async def get_available_counts(session: AsyncSession, skus: list["Sku"]) -> dict[int, int]:
    """全部 SKU 的近似库存,一次批量算完。"""
    if _provider is None:
        raise RuntimeError("inventory provider 未注册:入口必须先执行 wire_modules()")
    return await _provider(session, skus)
