from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app

from app.core.audit import AuditMiddleware
from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.errors import init_sentry, install_error_handlers
from app.core.logging import setup_logging
from app.core.observability import ObservabilityMiddleware
from app.core.security_headers import SecurityHeadersMiddleware


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    init_sentry()
    settings = get_settings()
    if settings.environment == "dev" and settings.bootstrap_admin_password:
        from app.core.db import get_sessionmaker
        from app.modules.adminapi.service import ensure_bootstrap_admin

        async with get_sessionmaker()() as session:
            await ensure_bootstrap_admin(session, settings.bootstrap_admin_password)
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="SuperDL API",
        version="0.1.0",
        lifespan=lifespan,
        # 用户端与管理端共用一份 OpenAPI(orval 按 tag 分组生成)
    )
    install_error_handlers(app)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(ObservabilityMiddleware)
    app.add_middleware(AuditMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/healthz", tags=["infra"], include_in_schema=False)
    async def healthz() -> dict[str, str]:
        """liveness:进程活着即可,不探依赖(避免 DB 抖动引发重启风暴)。"""
        return {"status": "ok"}

    @app.get("/readyz", tags=["infra"], include_in_schema=False)
    async def readyz() -> dict[str, str]:
        """readiness:探 DB,失败摘流量。"""
        from fastapi.responses import JSONResponse
        from sqlalchemy import text

        from app.core.db import get_sessionmaker

        try:
            async with get_sessionmaker()() as session:
                await session.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse(status_code=503, content={"status": "db_unavailable"})  # type: ignore[return-value]
        return {"status": "ready"}

    # /metrics:配置了 SUPERDL_METRICS_TOKEN 即要求 Bearer(prod 校验强制配置)
    metrics_app = make_asgi_app()

    async def metrics_guard(scope, receive, send):
        from starlette.datastructures import Headers
        from starlette.responses import PlainTextResponse

        token = get_settings().metrics_token
        if token and Headers(scope=scope).get("authorization") != f"Bearer {token}":
            await PlainTextResponse("unauthorized", status_code=401)(scope, receive, send)
            return
        await metrics_app(scope, receive, send)

    app.mount("/metrics", metrics_guard)

    _register_module_routers(app)
    wire_modules()
    return app


def _register_module_routers(app: FastAPI) -> None:
    """各业务模块的路由注册。随 WP 推进逐个接入。"""
    from app.modules.account.router import router as account_router
    from app.modules.adminapi.router import router as admin_router
    from app.modules.billing.router import router as billing_router
    from app.modules.billing.webhooks_router import router as webhooks_router
    from app.modules.catalog.router import router as catalog_router
    from app.modules.metering.router import router as metering_router
    from app.modules.notify.router import router as notify_router
    from app.modules.orchestrator.disks_router import router as disks_router
    from app.modules.orchestrator.router import router as orchestrator_router

    app.include_router(account_router, prefix="/api/v1")
    app.include_router(catalog_router, prefix="/api/v1")
    app.include_router(orchestrator_router, prefix="/api/v1")
    app.include_router(disks_router, prefix="/api/v1")
    app.include_router(billing_router, prefix="/api/v1")
    app.include_router(webhooks_router, prefix="/api/v1")
    app.include_router(metering_router, prefix="/api/v1")
    app.include_router(notify_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/admin/v1")


def wire_modules() -> None:
    """跨模块运行时接线:outbox handlers + 库存 provider + 计费边监听。双入口共用。"""
    from app.modules.billing.edge_listener import register_billing_edge_listener
    from app.modules.orchestrator import handlers as _handlers  # noqa: F401 注册 outbox handlers
    from app.modules.orchestrator.service import register_inventory_provider

    register_inventory_provider()
    register_billing_edge_listener()


app = create_app()
