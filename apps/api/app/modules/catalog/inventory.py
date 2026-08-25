"""近似库存:市场页展示的可售实例数,每请求按节点台账(node_specs,巡检 60s 写)直接算;
创建以 K8s 调度结果为准。

真实估算由 orchestrator 在 wire_modules() 时注入 provider(catalog 不反向依赖 orchestrator);
未接线即查询是装配 bug,直接报错。台账只有几十行、一次 SELECT + Python 循环,不配缓存。
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.modules.catalog.models import Sku

# (session, skus) -> {sku_id: 可售实例数};批量接口,一次调用算完全部 SKU
InventoryProvider = Callable[[AsyncSession, list["Sku"]], Awaitable[dict[int, int]]]

_provider: InventoryProvider | None = None


def register_inventory_provider(provider: InventoryProvider) -> None:
    global _provider
    _provider = provider


async def get_available_counts(session: AsyncSession, skus: list["Sku"]) -> dict[int, int]:
    """全部 SKU 的近似库存:直接调 provider,一次批量算完。"""
    if _provider is None:
        raise RuntimeError("inventory provider 未注册:入口必须先执行 wire_modules()")
    return await _provider(session, skus)
