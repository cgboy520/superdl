"""跨模块运行时接线:outbox handlers + 库存 provider + 计费边监听;serve / worker 双入口共用。"""


def wire_modules() -> None:
    """执行接线(幂等)。"""
    from app.modules.billing.edge_listener import register_billing_edge_listener
    from app.modules.catalog import prewarm as _prewarm  # noqa: F401
    from app.modules.catalog.inventory import register_inventory_provider
    from app.modules.nodes import handlers as _node_handlers  # noqa: F401
    from app.modules.notify import service as _notify_service  # noqa: F401
    from app.modules.orchestrator import handlers as _handlers  # noqa: F401
    from app.modules.orchestrator.service import estimate_available_many
    from app.modules.services import handlers as _service_handlers  # noqa: F401
    from app.modules.services.service import register_service_listeners

    register_inventory_provider(estimate_available_many)
    register_billing_edge_listener()
    register_service_listeners()
