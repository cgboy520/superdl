from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app

from app.core.audit import AuditMiddleware
from app.core.body_limit import RequestBodyLimitMiddleware
from app.core.config import get_settings, unknown_superdl_env_keys
from app.core.db import dispose_engine
from app.core.edge_guard import EdgeGuardMiddleware
from app.core.errors import Uniform500Middleware, install_error_handlers
from app.core.logging import get_logger, setup_logging
from app.core.observability import ObservabilityMiddleware
from app.core.security_headers import SecurityHeadersMiddleware
from app.wiring import wire_modules


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    settings = get_settings()
    from app.core.metrics import RUNTIME_CONFIG

    RUNTIME_CONFIG.labels(
        environment=settings.environment,
        k8s_backend=settings.k8s_backend,
        payment_mock=str(settings.payment_mock).lower(),
    ).set(1)
    log = get_logger("app.lifespan")
    unknown_keys = unknown_superdl_env_keys()
    if unknown_keys:
        log.warning(
            "unknown_superdl_env_vars",
            keys=unknown_keys,
            hint="这些 SUPERDL_* 变量不匹配任何配置项,将被忽略;请核对拼写",
        )
    if settings.environment == "prod":
        if not settings.alertmanager_token:
            log.warning(
                "alertmanager_token_missing",
                hint="Alertmanager webhook 将一律 401;配 SUPERDL_ALERTMANAGER_TOKEN 后告警才进平台",
            )
        if "localhost" in settings.prometheus_url or "127.0.0.1" in settings.prometheus_url:
            log.warning(
                "prometheus_url_localhost",
                prometheus_url=settings.prometheus_url,
                hint="监控代理仍指向本地默认地址:计费不受影响,但用量面板与对账全空",
            )
        from app.core.db import get_sessionmaker
        from app.core.platform_config import (
            assert_prod_compliance_gates,
            assert_prod_image_allowlist,
            compute_config_warnings,
            env_layer_problems,
            get_runtime_config,
        )

        async with get_sessionmaker()() as session:
            cfg = await get_runtime_config(session)
        missing = [k for k in ("cluster_server_url", "cluster_join_token") if not getattr(cfg, k)]
        if missing:
            log.error(
                "cluster_config_missing",
                keys=missing,
                hint="管理端「平台配置 · 集群接入」录入;加节点将被 409 拦截",
            )
        from app.modules.billing import service as billing_service

        for w in (
            *compute_config_warnings(cfg, settings.environment),
            *billing_service.payment_config_warnings(cfg, settings.environment),
        ):
            (log.error if w.level == "error" else log.warning)(
                "config_warning", key=w.key, hint=w.message
            )
        assert_prod_compliance_gates(cfg, settings.environment)
        assert_prod_image_allowlist(cfg, settings.environment)
        env_problems = env_layer_problems()
        if env_problems:
            if settings.environment == "prod":
                raise RuntimeError("部署层平台配置格式不合格,拒绝启动:" + ";".join(env_problems))
            log.error("config_env_invalid", problems=env_problems)
    from app.core.db import get_sessionmaker
    from app.modules.billing.service import assert_billing_identity

    async with get_sessionmaker()() as session:
        await assert_billing_identity(session)
    yield
    from app.modules.metering import prom as metering_prom

    await metering_prom.close_client()
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    is_prod = settings.environment == "prod"
    app = FastAPI(
        title="SuperDL API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if is_prod else "/docs",
        redoc_url=None if is_prod else "/redoc",
        openapi_url=None if is_prod else "/openapi.json",
    )
    install_error_handlers(app)
    app.add_middleware(Uniform500Middleware)
    app.add_middleware(RequestBodyLimitMiddleware)
    app.add_middleware(ObservabilityMiddleware)
    app.add_middleware(AuditMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Requested-With"],
        expose_headers=["X-Request-ID", "X-Idempotent-Replay"],
    )
    app.add_middleware(EdgeGuardMiddleware)

    @app.get("/healthz", tags=["infra"], include_in_schema=False)
    async def healthz() -> dict[str, str]:
        """liveness:进程活着即可,不探依赖。"""
        return {"status": "ok"}

    @app.get("/readyz", tags=["infra"], include_in_schema=False)
    async def readyz() -> JSONResponse:
        """readiness:探 DB + schema 版本必须等于代码 head,否则 503。"""
        from sqlalchemy import text

        from app.core.db import get_sessionmaker, schema_state

        try:
            async with get_sessionmaker()() as session:
                rows = (
                    (await session.execute(text("SELECT version_num FROM alembic_version")))
                    .scalars()
                    .all()
                )
            state = schema_state(list(rows))
        except Exception:
            state = "db_unavailable"
        prev = getattr(app.state, "readyz_state", "ready")
        if state != prev:
            get_logger("app.readyz").warning("readyz_state_change", previous=prev, current=state)
        app.state.readyz_state = state
        if state != "ready":
            return JSONResponse(status_code=503, content={"status": state})
        return JSONResponse(content={"status": "ready"})

    metrics_app = make_asgi_app()

    async def metrics_guard(scope, receive, send):
        from starlette.datastructures import Headers
        from starlette.responses import PlainTextResponse

        from app.core.http import bearer_matches

        token = get_settings().metrics_token
        if token and not bearer_matches(Headers(scope=scope).get("authorization"), token):
            await PlainTextResponse(
                "unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )(scope, receive, send)
            return
        await metrics_app(scope, receive, send)

    app.mount("/metrics", metrics_guard)

    _register_module_routers(app)
    wire_modules()
    return app


def _register_module_routers(app: FastAPI) -> None:
    from app.modules.account.router import router as account_router
    from app.modules.adminapi.router import router as admin_router
    from app.modules.billing.router import router as billing_router
    from app.modules.billing.webhooks_router import router as webhooks_router
    from app.modules.catalog.router import router as catalog_router
    from app.modules.legal.router import router as legal_router
    from app.modules.metering.router import router as metering_router
    from app.modules.nodes.enroll_router import router as node_enroll_router
    from app.modules.notify.router import router as notify_router
    from app.modules.orchestrator.disks_router import router as disks_router
    from app.modules.orchestrator.router import router as orchestrator_router
    from app.modules.services.endpoint_auth_router import router as endpoint_auth_router
    from app.modules.services.router import router as services_router
    from app.modules.tickets.router import router as tickets_router

    app.include_router(account_router, prefix="/api/v1")
    app.include_router(catalog_router, prefix="/api/v1")
    app.include_router(orchestrator_router, prefix="/api/v1")
    app.include_router(disks_router, prefix="/api/v1")
    app.include_router(services_router, prefix="/api/v1")
    app.include_router(billing_router, prefix="/api/v1")
    app.include_router(webhooks_router, prefix="/api/v1")
    app.include_router(node_enroll_router, prefix="/api/v1")
    app.include_router(metering_router, prefix="/api/v1")
    app.include_router(notify_router, prefix="/api/v1")
    app.include_router(tickets_router, prefix="/api/v1")
    app.include_router(legal_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/admin/v1")
    app.include_router(endpoint_auth_router, prefix="/api/internal/v1")


app = create_app()
