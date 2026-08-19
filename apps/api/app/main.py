from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app

from app.core.audit import AuditMiddleware
from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.errors import install_error_handlers
from app.core.logging import setup_logging


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
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
        return {"status": "ok"}

    app.mount("/metrics", make_asgi_app())

    _register_module_routers(app)
    return app


def _register_module_routers(app: FastAPI) -> None:
    """各业务模块的路由注册。随 WP 推进逐个接入。"""
    from app.modules.account.router import router as account_router

    app.include_router(account_router, prefix="/api/v1")


app = create_app()
