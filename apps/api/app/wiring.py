"""跨模块运行时接线:outbox handlers + 库存 provider + 计费边监听。serve / worker 双入口共用。

独立成模块而非挂在 app.main:worker 入口只需接线,不应 import 整个 FastAPI app
(经 app.main 引入会在 worker 进程里触发模块级 create_app())。
"""


def wire_modules() -> None:
    """跨模块运行时接线:outbox handlers + 库存 provider + 计费边监听。双入口共用。"""
    from app.modules.billing.edge_listener import register_billing_edge_listener
    from app.modules.catalog import prewarm as _prewarm  # noqa: F401 注册 image.prewarm handler
    from app.modules.catalog.inventory import register_inventory_provider
    from app.modules.nodes import handlers as _node_handlers  # noqa: F401 注册 node.cordon handler
    from app.modules.notify import service as _notify_service  # noqa: F401 注册 notify.sms handler
    from app.modules.orchestrator import handlers as _handlers  # noqa: F401 注册 outbox handlers
    from app.modules.orchestrator.service import (
        estimate_available_many,
        register_endpoint_auth_cache_listener,
    )

    register_inventory_provider(estimate_available_many)
    register_billing_edge_listener()
    register_endpoint_auth_cache_listener()
