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
    if settings.environment == "prod":
        # cluster 键不做启动 fail-fast(推荐经管理端 DB 覆盖层维护,启动查 env 会误报)
        # → DB 就绪后查 effective 配置,缺键打 error;集群页红牌与加节点 409 门禁兜底
        from app.core.db import get_sessionmaker
        from app.core.logging import get_logger
        from app.core.platform_config import get_effective_platform_config

        async with get_sessionmaker()() as session:
            cfg = await get_effective_platform_config(session)
        missing = [k for k in ("cluster_server_url", "cluster_join_token") if not cfg.get(k)]
        if missing:
            get_logger("app.lifespan").error(
                "cluster_config_missing",
                keys=missing,
                hint="管理端「平台配置 · 集群接入」录入;加节点将被 409 拦截",
            )
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    is_prod = settings.environment == "prod"
    app = FastAPI(
        title="SuperDL API",
        version="0.1.0",
        lifespan=lifespan,
        # 用户端与管理端共用一份 OpenAPI(orval 按 tag 分组生成)
        # 生产关掉三条文档路由:API host 在 Ingress 上是公网直出,任何人免登录即可从
        # /docs 或 /openapi.json 拿到全部管理端路径、字段名与取值范围(调账、补单、
        # 平台配置都在里面)。只关 docs_url 不够 —— openapi_url 仍会把整份 schema 吐出去。
        # export_openapi 走的是 app.openapi() 直取 schema,不经这些路由,契约闸门不受影响;
        # 顺带让 security_headers 里给 Swagger UI 开的那个 CSP 豁免在生产失效。
        docs_url=None if is_prod else "/docs",
        redoc_url=None if is_prod else "/redoc",
        openapi_url=None if is_prod else "/openapi.json",
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
        import secrets

        from starlette.datastructures import Headers
        from starlette.responses import PlainTextResponse

        token = get_settings().metrics_token
        # 常量时间比较:逐字节短路比较可被计时探测出 token 前缀
        if token and not secrets.compare_digest(
            Headers(scope=scope).get("authorization") or "", f"Bearer {token}"
        ):
            await PlainTextResponse("unauthorized", status_code=401)(scope, receive, send)
            return
        await metrics_app(scope, receive, send)

    app.mount("/metrics", metrics_guard)

    _register_module_routers(app)
    wire_modules()
    return app


def _register_module_routers(app: FastAPI) -> None:
    """各业务模块的路由注册。"""
    from app.modules.account.router import router as account_router
    from app.modules.adminapi.router import router as admin_router
    from app.modules.billing.router import router as billing_router
    from app.modules.billing.webhooks_router import router as webhooks_router
    from app.modules.catalog.router import router as catalog_router
    from app.modules.metering.router import router as metering_router
    from app.modules.nodes.enroll_router import router as node_enroll_router
    from app.modules.notify.router import router as notify_router
    from app.modules.orchestrator.disks_router import router as disks_router
    from app.modules.orchestrator.router import router as orchestrator_router

    app.include_router(account_router, prefix="/api/v1")
    app.include_router(catalog_router, prefix="/api/v1")
    app.include_router(orchestrator_router, prefix="/api/v1")
    app.include_router(disks_router, prefix="/api/v1")
    app.include_router(billing_router, prefix="/api/v1")
    app.include_router(webhooks_router, prefix="/api/v1")
    app.include_router(node_enroll_router, prefix="/api/v1")
    app.include_router(metering_router, prefix="/api/v1")
    app.include_router(notify_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/admin/v1")


def wire_modules() -> None:
    """跨模块运行时接线:outbox handlers + 库存 provider + 计费边监听。双入口共用。"""
    from app.modules.billing.edge_listener import register_billing_edge_listener
    from app.modules.catalog import prewarm as _prewarm  # noqa: F401 注册 image.prewarm handler
    from app.modules.catalog.inventory import register_inventory_provider
    from app.modules.nodes import handlers as _node_handlers  # noqa: F401 注册 node.cordon handler
    from app.modules.orchestrator import handlers as _handlers  # noqa: F401 注册 outbox handlers
    from app.modules.orchestrator.service import estimate_available

    register_inventory_provider(estimate_available)
    register_billing_edge_listener()


app = create_app()
