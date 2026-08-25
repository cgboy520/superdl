"""跨模块运行时接线:outbox handlers + 库存 provider + 计费边监听。serve / worker 双入口共用。

独立成模块而非挂在 app.main:worker 入口只需接线,不应 import 整个 FastAPI app
(经 app.main 引入会在 worker 进程里触发模块级 create_app())。
"""


def wire_modules() -> None:
    """跨模块运行时接线:outbox handlers + 库存 provider + 计费边监听。双入口共用。"""
    from app.modules.billing.edge_listener import register_billing_edge_listener
    from app.modules.catalog import prewarm as _prewarm  # noqa: F401 注册 image.prewarm handler
    from app.modules.catalog.inventory import register_inventory_provider
    from app.modules.catalog.service import skus_signature
    from app.modules.nodes import handlers as _node_handlers  # noqa: F401 注册 node.cordon handler
    from app.modules.nodes.service import node_specs_signature
    from app.modules.notify import service as _notify_service  # noqa: F401 注册 notify.sms handler
    from app.modules.orchestrator import handlers as _handlers  # noqa: F401 注册 outbox handlers
    from app.modules.orchestrator.service import estimate_available_many

    async def _inventory_signature(session):
        """库存缓存签名 = 台账签名 + SKU 签名(P2):SKU 改 型号/池/上下架也触发重算。"""
        node_sig = await node_specs_signature(session)
        sku_sig = await skus_signature(session)
        return (*node_sig, *sku_sig)

    register_inventory_provider(estimate_available_many, _inventory_signature)
    register_billing_edge_listener()
