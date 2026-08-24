from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app

from app.core.audit import AuditMiddleware
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
    log = get_logger("app.lifespan")
    # 幽灵 SUPERDL_* 变量(拼写错误/改名残留)会被静默忽略:启动即告警,不 fail
    unknown_keys = unknown_superdl_env_keys()
    if unknown_keys:
        log.warning(
            "unknown_superdl_env_vars",
            keys=unknown_keys,
            hint="这些 SUPERDL_* 变量不匹配任何配置项,将被忽略;请核对拼写",
        )
    if settings.environment != "prod":
        # 忘记显式设置 SUPERDL_ENVIRONMENT 的生产部署会以 dev 默认值裸奔:至少留一条醒目告警
        log.warning(
            "non_prod_environment",
            environment=settings.environment,
            hint="生产部署必须显式设置 SUPERDL_ENVIRONMENT=prod(prod 有配置 fail-fast 校验)",
        )
    # 一次性引导:仅 dev,配置了口令且 admin_users 为空时创建首个超管
    if settings.bootstrap_admin_password:
        if settings.environment != "dev":
            log.warning(
                "bootstrap_admin_skipped",
                environment=settings.environment,
                hint="SUPERDL_BOOTSTRAP_ADMIN_PASSWORD 仅 dev 生效(prod 由配置校验直接拒启动)",
            )
        else:
            if len(settings.bootstrap_admin_password) < 12:
                raise RuntimeError(
                    "SUPERDL_BOOTSTRAP_ADMIN_PASSWORD 口令长度至少 12 位"
                    "(与管理端创建管理员的约束一致);这是一次性引导变量,首个管理员创建成功后"
                    "请立即从环境变量中删除"
                )
            from app.core.db import get_sessionmaker
            from app.modules.adminapi.service import ensure_bootstrap_admin

            async with get_sessionmaker()() as session:
                await ensure_bootstrap_admin(session, settings.bootstrap_admin_password)
    if settings.environment == "prod":
        # cluster 键不做启动 fail-fast(经 DB 覆盖层维护,查 env 会误报):
        # DB 就绪后查 effective 配置,缺键打 error;集群页红牌与加节点 409 兜底
        from app.core.db import get_sessionmaker
        from app.core.platform_config import get_effective_platform_config

        async with get_sessionmaker()() as session:
            cfg = await get_effective_platform_config(session)
        missing = [k for k in ("cluster_server_url", "cluster_join_token") if not cfg.get(k)]
        if missing:
            log.error(
                "cluster_config_missing",
                keys=missing,
                hint="管理端「平台配置 · 集群接入」录入;加节点将被 409 拦截",
            )
    yield
    # Prometheus 代理客户端是全局单例(连接池),进程退出前显式关闭
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
        # 用户端与管理端共用一份 OpenAPI(orval 按 tag 分组生成)。
        # 生产三条路由同时关:只关 docs_url 时 openapi_url 仍会吐出整份管理端 schema。
        # export_openapi 直取 app.openapi(),不经路由。
        docs_url=None if is_prod else "/docs",
        redoc_url=None if is_prod else "/redoc",
        openapi_url=None if is_prod else "/openapi.json",
    )
    install_error_handlers(app)
    # 最先注册 = 最内层:未捕获异常在此渲染 500 并沿链回传,
    # 安全头(SecurityHeaders)与 x-request-id(Observability)才能挂上错误响应
    app.add_middleware(Uniform500Middleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(ObservabilityMiddleware)
    app.add_middleware(AuditMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # CORS 默认不暴露自定义响应头;前端 fetch 需读到该头做全链路追踪
        expose_headers=["X-Request-ID"],
    )
    # 最外层:边缘收口(prod 下 /api/admin 与 /metrics 不从公网 api 域暴露)
    app.add_middleware(EdgeGuardMiddleware)

    @app.get("/healthz", tags=["infra"], include_in_schema=False)
    async def healthz() -> dict[str, str]:
        """liveness:进程活着即可,不探依赖。"""
        return {"status": "ok"}

    @app.get("/readyz", tags=["infra"], include_in_schema=False)
    async def readyz() -> JSONResponse:
        """readiness:探 DB + 比对 schema 版本(迁移漏跑的新代码不就绪,防带病放量;
        滚动窗口内「老代码+新 schema」按 expand-only 约定放行)。"""
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
        # 探针每 5s 一次:只在状态翻转时留日志,避免滚动窗口刷量
        prev = getattr(app.state, "readyz_state", "ready")
        if state != prev:
            get_logger("app.readyz").warning("readyz_state_change", previous=prev, current=state)
        app.state.readyz_state = state
        if state != "ready":
            return JSONResponse(status_code=503, content={"status": state})
        return JSONResponse(content={"status": "ready"})

    # /metrics:配置了 SUPERDL_METRICS_TOKEN 即要求 Bearer(prod 校验强制配置)
    metrics_app = make_asgi_app()

    async def metrics_guard(scope, receive, send):
        import secrets

        from starlette.datastructures import Headers
        from starlette.responses import PlainTextResponse

        token = get_settings().metrics_token
        # 常量时间比较,防计时探测出 token 前缀;
        # 先 encode:compare_digest 的 str 入参遇非 ASCII(如畸形头)会抛 TypeError
        authorization = Headers(scope=scope).get("authorization") or ""
        if token and not secrets.compare_digest(authorization.encode(), f"Bearer {token}".encode()):
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
    """各业务模块的路由注册。"""
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
    from app.modules.tickets.router import router as tickets_router

    app.include_router(account_router, prefix="/api/v1")
    app.include_router(catalog_router, prefix="/api/v1")
    app.include_router(orchestrator_router, prefix="/api/v1")
    app.include_router(disks_router, prefix="/api/v1")
    app.include_router(billing_router, prefix="/api/v1")
    app.include_router(webhooks_router, prefix="/api/v1")
    app.include_router(node_enroll_router, prefix="/api/v1")
    app.include_router(metering_router, prefix="/api/v1")
    app.include_router(notify_router, prefix="/api/v1")
    app.include_router(tickets_router, prefix="/api/v1")
    app.include_router(legal_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/admin/v1")


app = create_app()
