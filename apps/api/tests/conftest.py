"""测试基建:testcontainers 起真 PG18,create_all 建表,函数级 TRUNCATE 隔离。"""

import os
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

if TYPE_CHECKING:
    from app.core.k8s.fake import FakeOrchestrator

# 静态测试环境变量:导入即就位(不挂 fixture),强制赋值(非 setdefault)
os.environ["SUPERDL_ENVIRONMENT"] = "test"
os.environ["SUPERDL_K8S_BACKEND"] = "fake"  # 单测一律 FakeOrchestrator,隔离本地 .env 的 real 配置
# 超时钉死 5 分钟
os.environ["SUPERDL_CREATING_TIMEOUT_SECONDS"] = "300"
# 测试签名密钥 ≥32 字节
os.environ["SUPERDL_JWT_SECRET"] = "test-jwt-secret-32-bytes-minimum!!"


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    with PostgresContainer("postgres:18", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        os.environ["SUPERDL_DATABASE_URL"] = url
        # 环境变量就位后再清缓存
        from app.core.config import get_settings

        get_settings.cache_clear()
        yield url
        get_settings.cache_clear()
    # docker-py 客户端显式关闭
    pg.get_docker_client().client.close()


@pytest.fixture(scope="session")
async def engine(pg_url: str) -> AsyncIterator[AsyncEngine]:
    from app.core.db import code_schema_head, dispose_engine, get_engine
    from app.models_registry import Base

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # alembic_version 盖章到代码 head(/readyz 比对用;随会话存活)
        await conn.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        await conn.execute(text("DELETE FROM alembic_version"))
        await conn.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
            {"v": code_schema_head()},
        )
    yield engine
    await dispose_engine()


@pytest.fixture
async def sm(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """函数级 sessionmaker;测试结束 TRUNCATE 全部表保证隔离。"""
    from app.core.db import get_sessionmaker
    from app.models_registry import Base

    smaker = get_sessionmaker()
    # 集群能力缓存预置健康态(等价 worker 已跑过一轮巡检)
    from app.core.k8s.base import (
        INSTANCE_DISK_STORAGE_CLASS,
        JUICEFS_STORAGE_CLASS,
        ClusterProbe,
    )
    from app.modules.nodes.service import save_cluster_probe

    async with smaker() as session:
        await save_cluster_probe(
            session,
            ClusterProbe(
                api_reachable=True,
                k8s_version="v1.36.2+rke2r1",
                distro="rke2",
                hami_ready=True,
                kata_runtimeclass=True,  # dedicated 档门禁的正例基线
                storage_classes=(INSTANCE_DISK_STORAGE_CLASS, JUICEFS_STORAGE_CLASS),
            ),
        )
        await session.commit()
    # 法务文档预置(等价迁移已跑)
    from tests.legal_preset import seed_preset_docs

    async with smaker() as session:
        await seed_preset_docs(session)
        await session.commit()
    # extAuth 鉴权缓存是进程内态,逐用例清空
    from app.modules.services import service as services_service

    services_service.clear_endpoint_auth_cache()
    # 审计 fail-closed 闸门同样是进程内态
    from app.core import audit as audit_mod

    audit_mod.reset_audit_gate()
    yield smaker

    async with engine.begin() as conn:
        tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        if tables:
            await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def client(sm: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncClient]:
    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def fake() -> Iterator["FakeOrchestrator"]:
    """注入 FakeOrchestrator(auto_ready=False,时序由用例驱动),收尾恢复默认。"""
    from app.core.k8s import set_orchestrator
    from app.core.k8s.fake import FakeOrchestrator

    orch = FakeOrchestrator(auto_ready=False)
    set_orchestrator(orch)
    yield orch
    set_orchestrator(None)


@pytest.fixture
def fake_auto_ready() -> Iterator["FakeOrchestrator"]:
    """auto_ready=True 的 FakeOrchestrator;与基线 fake 互斥,同一用例只取其一。"""
    from app.core.k8s import set_orchestrator
    from app.core.k8s.fake import FakeOrchestrator

    orch = FakeOrchestrator(auto_ready=True)
    set_orchestrator(orch)
    yield orch
    set_orchestrator(None)


@pytest.fixture(autouse=True)
def _reset_prom_client() -> Iterator[None]:
    """逐用例清空进程内 Prometheus 客户端。"""
    yield
    from app.modules.metering import prom

    prom.set_client(None)
