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
    # 生效的加固面上报成指标:environment/后端/mock 支付的漂移(prod 跑成 dev 口径)
    # 在 Prometheus 侧可告警,不靠人肉巡检配置页
    from app.core.metrics import RUNTIME_CONFIG

    RUNTIME_CONFIG.labels(
        environment=settings.environment,
        k8s_backend=settings.k8s_backend,
        payment_mock=str(settings.payment_mock).lower(),
    ).set(1)
    log = get_logger("app.lifespan")
    # 幽灵 SUPERDL_* 变量(拼写错误/改名残留)会被静默忽略:启动即告警,不 fail
    unknown_keys = unknown_superdl_env_keys()
    if unknown_keys:
        log.warning(
            "unknown_superdl_env_vars",
            keys=unknown_keys,
            hint="这些 SUPERDL_* 变量不匹配任何配置项,将被忽略;请核对拼写",
        )
    if settings.environment == "prod":
        # 非阻断项只打告警,不 fail-fast:webhook 未配 token 本就拒收,指标子系统优雅降级
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
        # cluster 键经 DB 覆盖层维护,查 env 会误报:改查 effective 配置,缺键只打 error
        from app.core.db import get_sessionmaker
        from app.core.platform_config import (
            assert_prod_compliance_gates,
            compute_config_warnings,
            get_effective_platform_config,
        )

        async with get_sessionmaker()() as session:
            cfg = await get_effective_platform_config(session)
        missing = [k for k in ("cluster_server_url", "cluster_join_token") if not cfg.get(k)]
        if missing:
            log.error(
                "cluster_config_missing",
                keys=missing,
                hint="管理端「平台配置 · 集群接入」录入;加节点将被 409 拦截",
            )
        # 安全开关允许在 prod 关闭(运营决定),但启动日志与管理端配置页都要看得见
        for w in compute_config_warnings(cfg, settings.environment):
            (log.error if w.level == "error" else log.warning)(
                "config_warning", key=w.key, hint=w.message
            )
        # 合规闸门 fail-fast(实现在 platform_config.assert_prod_compliance_gates)
        assert_prod_compliance_gates(cfg, settings.environment)
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
    # 请求体硬上限(内层防御;外层在 Envoy requestBuffer):流式计数,超限 413 短路。
    # 注册在次内层:其外的中间件均不读请求流,攻击面在抵达路由前被掐断;
    # 413 响应沿链外返仍带安全头 / x-request-id / CORS 头(浏览器才读得到错误体)
    app.add_middleware(RequestBodyLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(ObservabilityMiddleware)
    app.add_middleware(AuditMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        # 枚举实际使用面,不用通配:方法与头部收窄到双端真实请求
        # (X-Requested-With 是 refresh/logout 的 CSRF 纵深头,见 account/router)
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Requested-With"],
        # CORS 默认不暴露自定义响应头;前端 fetch 需读到这两个头做全链路追踪与重放区分
        expose_headers=["X-Request-ID", "X-Idempotent-Replay"],
    )
    # 最外层:边缘收口(prod 下 /api/admin 与 /metrics 不从公网 api 域暴露)
    app.add_middleware(EdgeGuardMiddleware)

    @app.get("/healthz", tags=["infra"], include_in_schema=False)
    async def healthz() -> dict[str, str]:
        """liveness:进程活着即可,不探依赖。"""
        return {"status": "ok"}

    @app.get("/readyz", tags=["infra"], include_in_schema=False)
    async def readyz() -> JSONResponse:
        """readiness:探 DB + 比对 schema 版本必须等于代码 head(停机发布模型,
        任何版本偏差都是部署事故,503 摘流)。"""
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
        # 探针每 5s 一次:只在状态翻转时留日志,避免状态抖动刷量
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
    # 网关 extAuth 回调:独立前缀 /api/internal,边缘收口(core/edge_guard)据此判定
    app.include_router(endpoint_auth_router, prefix="/api/internal/v1")


app = create_app()
