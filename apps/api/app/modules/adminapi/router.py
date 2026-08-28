"""管理端路由(门面):按域拆分为子路由文件,此处仅装配。

子路由:auth/admins/catalog/nodes/tenants/finance/ops/legal。
OpenAPI 契约(tags/operationId/路径/依赖)由 CI export_openapi 无 diff 锁定。
"""

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

# tags 只挂在各子路由上(门面再挂会与子路由叠加成 ["admin", "admin"])
router = APIRouter()

# 各子路由路径按域无交叉,装配顺序不影响匹配
router.include_router(router_auth.router)
router.include_router(router_ops.router)
router.include_router(router_admins.router)
router.include_router(router_catalog.router)
router.include_router(router_nodes.router)
router.include_router(router_finance.router)
router.include_router(router_tenants.router)
router.include_router(router_legal.router)
