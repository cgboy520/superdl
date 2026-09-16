"""Admin sub-router assembly."""

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

router = APIRouter()

router.include_router(router_auth.router)
router.include_router(router_ops.router)
router.include_router(router_admins.router)
router.include_router(router_catalog.router)
router.include_router(router_nodes.router)
router.include_router(router_finance.router)
router.include_router(router_tenants.router)
router.include_router(router_legal.router)
