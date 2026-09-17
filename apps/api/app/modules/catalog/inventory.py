"""Approximate stock provider registration and batch call; register before calling."""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.modules.catalog.models import Sku

InventoryProvider = Callable[[AsyncSession, list["Sku"]], Awaitable[dict[int, int]]]

_provider: InventoryProvider | None = None


def register_inventory_provider(provider: InventoryProvider) -> None:
    global _provider
    _provider = provider


async def get_available_counts(session: AsyncSession, skus: list["Sku"]) -> dict[int, int]:
    """Approximate stock of every SKU, computed in one batch."""
    if _provider is None:
        raise RuntimeError(
            "inventory provider not registered: the entry point must run wire_modules() first"
        )
    return await _provider(session, skus)
