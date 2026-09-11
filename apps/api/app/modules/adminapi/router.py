"""管理端路由门面:装配 auth/admins/catalog/nodes/tenants/finance/ops/legal 子路由。"""

from fastapi import APIRouter

from app.modules.adminapi import (
    router_admins,
    router_auth,
    router_catalog,
    router_finance,
    router_legal,
    router_nodes,
    router_ops,
    router_tenants,
)

# tags 只挂在各子路由上
router = APIRouter()

router.include_router(router_auth.router)
router.include_router(router_ops.router)
router.include_router(router_admins.router)
router.include_router(router_catalog.router)
router.include_router(router_nodes.router)
router.include_router(router_finance.router)
router.include_router(router_tenants.router)
router.include_router(router_legal.router)
